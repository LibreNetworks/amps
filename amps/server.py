# amps/server.py

import time
import logging
from flask import Flask, Response, request, abort, url_for, jsonify
from amps import ffmpeg_utils
from amps.api import api_bp

START_TIME = time.time()

def create_app(config: dict) -> Flask:
    """
    Creates and configures the Flask application.
    """
    app = Flask(__name__)
    app.config.update(config)

    # Register API blueprint
    app.register_blueprint(api_bp)

    @app.before_request
    def auth_middleware():
        """Enforces token authentication on protected routes."""
        if request.path == '/metrics' or not app.config['auth']['enabled']:
            return  # Skip auth for metrics or if auth is disabled

        token = request.headers.get('X-Amps-Token') or request.args.get('token')
        if not token or token != app.config['auth']['token']:
            logging.warning(f"Unauthorized access attempt from {request.remote_addr}")
            abort(401, description="Unauthorized: Valid token required.")

    @app.route('/playlist.m3u')
    def generate_playlist():
        """Generates a dynamic M3U playlist."""
        m3u_content = ['#EXTM3U']
        base_url = request.host_url

        # Add token to URL if auth is enabled
        auth_query = f"?token={app.config['auth']['token']}" if app.config['auth']['enabled'] else ""

        # Use the in-memory map which can be modified by the API
        stream_map = app.config.get('stream_map', {})

        for stream in sorted(stream_map.values(), key=lambda s: s['id']):
            stream_url = f"{base_url.rstrip('/')}{url_for('stream_media', stream_id=stream['id'])}{auth_query}"
            tvg_name = stream.get('tvg_name') or stream.get('name')
            attributes = [f'tvg-id="{stream["id"]}"']
            if tvg_name:
                attributes.append(f'tvg-name="{tvg_name}"')
            if stream.get('logo'):
                attributes.append(f'tvg-logo="{stream["logo"]}"')
            if stream.get('group'):
                attributes.append(f'group-title="{stream["group"]}"')
            if stream.get('channel_number'):
                attributes.append(f'channel-number="{stream["channel_number"]}"')

            display_name = stream.get('name')
            m3u_content.append(f'#EXTINF:-1 {" ".join(attributes)},{display_name}')

            next_programs = stream.get('next_programs') or []
            if next_programs:
                next_program = next_programs[0]
                program_parts = []
                if next_program.get('title'):
                    program_parts.append(f'title="{next_program["title"]}"')
                if next_program.get('start'):
                    program_parts.append(f'start="{next_program["start"]}"')
                if next_program.get('description'):
                    program_parts.append(f'description="{next_program["description"]}"')
                if program_parts:
                    m3u_content.append(f'#EXTREM:AMP-NEXT {" ".join(program_parts)}')

            if stream.get('program_feed'):
                m3u_content.append(f'#EXTREM:AMP-PROGRAM-FEED url="{stream["program_feed"]}"')

            if stream.get('description'):
                m3u_content.append(f'#EXTREM:AMP-DESCRIPTION {stream["description"]}')

            m3u_content.append(stream_url)

        return Response('\n'.join(m3u_content), mimetype='application/vnd.apple.mpegurl')

    @app.route('/stream/<int:stream_id>')
    def stream_media(stream_id):
        """Looks up a stream, runs FFmpeg, and pipes the output."""
        stream_map = app.config.get('stream_map', {})
        stream_config = stream_map.get(stream_id)

        if not stream_config:
            abort(404, description=f"Stream with ID {stream_id} not found.")

        custom_ffmpeg = stream_config.get('custom_ffmpeg')
        ffmpeg_profile_name = stream_config.get('ffmpeg_profile')

        if custom_ffmpeg:
            ffmpeg_profile = app.config['ffmpeg_profiles'].get(ffmpeg_profile_name, {}) if ffmpeg_profile_name else {}
        else:
            if not ffmpeg_profile_name:
                abort(500, description=f"Stream {stream_id} is missing an ffmpeg_profile configuration.")
            ffmpeg_profile = app.config['ffmpeg_profiles'].get(ffmpeg_profile_name)
            if not ffmpeg_profile:
                abort(500, description=f"FFmpeg profile '{ffmpeg_profile_name}' not found for stream {stream_id}.")

        process = ffmpeg_utils.get_or_start_stream_process(stream_config, ffmpeg_profile)

        if not process:
            abort(500, description=f"Failed to start FFmpeg for stream {stream_id}.")

        def generate_chunks():
            try:
                # Read in chunks to stream the data
                while True:
                    chunk = process.stdout.read(4096)
                    if not chunk:
                        logging.warning(f"Stream {stream_id} ended or FFmpeg process died.")
                        break
                    yield chunk
            except Exception as e:
                logging.error(f"Error while streaming for stream {stream_id}: {e}")
            finally:
                logging.info(f"Client disconnected from stream {stream_id}.")
                # Note: We don't kill the process here, as other clients might be connected.
                # The process will be restarted on next request if it dies.

        # MPEG-TS is the transport stream format specified in ffmpeg_profiles
        return Response(generate_chunks(), mimetype='video/mp2t')

    @app.route('/metrics')
    def metrics():
        """Returns simple server metrics."""
        uptime_seconds = time.time() - START_TIME
        return jsonify({
            'uptime_seconds': uptime_seconds,
            'stream_count': len(app.config.get('stream_map', {})),
            'active_ffmpeg_processes': sum(1 for p_data in ffmpeg_utils.RUNNING_PROCESSES.values() if p_data['process'] and p_data['process'].poll() is None)
        })

    return app
