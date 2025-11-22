# amps/cli.py

import click
import logging
import subprocess
import sys
import shutil
from urllib.parse import urlencode

import requests

from amps.config_loader import load_config
from amps.server import create_app

@click.group()
def main_cli():
    """
    Amps - Advanced Media Playlist Server
    A dynamic M3U playlist and media streaming server powered by FFmpeg.
    """
    # Configure logging
    log_format = '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s'
    logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
    # Silence overly verbose libraries
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    # Create a separate logger for ffmpeg output
    logging.getLogger('ffmpeg')


def _build_base_url(app_config: dict) -> str:
    server_conf = app_config['server']
    host = server_conf['host']
    port = server_conf['port']
    return f"http://{host}:{port}"


@main_cli.command('serve')
@click.option('--config', default='config.yaml', help='Path to the YAML configuration file.', type=click.Path(exists=True))
def serve_command(config):
    """Starts the Amps Flask server."""
    app_config = load_config(config)
    app = create_app(app_config)

    server_conf = app_config['server']
    host = server_conf['host']
    port = server_conf['port']
    debug = server_conf['debug']

    print("---" * 10)
    print("⚡ Amps – Advanced Media Playlist Server")
    if app_config['auth']['enabled']:
        print("🔒 Authentication: Enabled")
    else:
        print("🔓 Authentication: Disabled")
    print(f"📡 Serving {len(app_config['stream_map'])} streams at http://{host}:{port}")
    scheduled_count = len(app_config.get('scheduled_streams', []))
    if scheduled_count:
        print(f"🕒 {scheduled_count} scheduled stream(s) configured")
    if debug:
        print("⚠️  Running in DEBUG mode. Do not use in production.")
    print("---" * 10)

    if debug:
        # Use Flask's built-in development server
        app.run(host=host, port=port, debug=True)
    else:
        # Use a production-ready WSGI server like Gunicorn
        # Note: For this to work, 'gunicorn' must be installed.
        try:
            from gunicorn.app.base import BaseApplication

            class StandaloneApplication(BaseApplication):
                def __init__(self, app, options=None):
                    self.options = options or {}
                    self.application = app
                    super().__init__()

                def load_config(self):
                    for key, value in self.options.items():
                        self.cfg.set(key.lower(), value)

                def load(self):
                    return self.application

            options = {
                'bind': f'{host}:{port}',
                'workers': 4, # A sensible default
                'threads': 4,
                'worker_class': 'gthread',
                'timeout': 120
            }
            StandaloneApplication(app, options).run()
        except ImportError:
            logging.error("Gunicorn not found. Please install it (`pip install gunicorn`) to run in production mode.")
            logging.warning("Falling back to Flask's development server. DO NOT USE IN PRODUCTION.")
            app.run(host=host, port=port)


@main_cli.command('list')
@click.option('--config', default='config.yaml', help='Path to the YAML configuration file.', type=click.Path(exists=True))
def list_command(config):
    """Lists all available streams from the configuration."""
    app_config = load_config(config)
    streams = app_config.get('streams', [])
    if not streams:
        click.echo("No streams found in the configuration.")
        return

    click.echo("Available Streams:")
    for stream in streams:
        profile = stream.get('ffmpeg_profile')
        custom = stream.get('custom_ffmpeg')

        if custom and profile:
            profile_label = f"{profile} (custom override)"
        elif custom and not profile:
            profile_label = "custom command"
        else:
            profile_label = profile or "—"

        logo = stream.get('logo') or "—"
        click.echo(f"  - ID: {stream['id']}, Name: {stream['name']}, Profile: {profile_label}, Logo: {logo}")


@main_cli.command('tuners')
@click.option('--config', default='config.yaml', help='Path to the YAML configuration file.', type=click.Path(exists=True))
def tuner_status_command(config):
    """Displays the current tuner/FFmpeg process status from the running server."""

    app_config = load_config(config)
    base_url = _build_base_url(app_config)
    headers = {}
    if app_config['auth']['enabled']:
        headers['X-Amps-Token'] = app_config['auth']['token']

    try:
        response = requests.get(f"{base_url}/api/tuners", headers=headers, timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        click.echo(f"Failed to fetch tuner status: {exc}")
        sys.exit(1)

    payload = response.json()
    tuners = payload.get('tuners', [])
    click.echo(f"Active tuners: {payload.get('active', 0)}/{payload.get('total', len(tuners))}")
    for tuner in tuners:
        status = 'running' if tuner.get('running') else 'stopped'
        started = tuner.get('started_at')
        started_label = f" started_at={started}" if started else ''
        click.echo(
            f"- Stream {tuner.get('stream_id')} [{tuner.get('variant')}]: {status}"
            f" (pid={tuner.get('pid')}){started_label}"
        )


@main_cli.command('shutdown')
@click.option('--config', default='config.yaml', help='Path to the YAML configuration file.', type=click.Path(exists=True))
def shutdown_command(config):
    """Requests a graceful server shutdown via the control API."""

    app_config = load_config(config)
    base_url = _build_base_url(app_config)
    headers = {}
    if app_config['auth']['enabled']:
        headers['X-Amps-Token'] = app_config['auth']['token']

    try:
        response = requests.post(f"{base_url}/api/shutdown", headers=headers, timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        click.echo(f"Failed to shut down server: {exc}")
        sys.exit(1)

    click.echo("Shutdown signal sent. The server will exit shortly.")


@main_cli.command('vlc')
@click.option('--config', default='config.yaml', help='Path to the YAML configuration file.', type=click.Path(exists=True))
@click.option('--stream-id', type=int, help='Open a specific stream ID instead of the full playlist.')
@click.option('--vlc-path', type=click.Path(), help='Path to the VLC binary if not on PATH.')
@click.option('--region', help='Optional region code to append to the URL.')
def vlc_command(config, stream_id, vlc_path, region):
    """Launches VLC pointing at the running Amps playlist or a specific channel."""

    app_config = load_config(config)
    base_url = _build_base_url(app_config)

    params = {}
    if app_config['auth']['enabled']:
        params['token'] = app_config['auth']['token']
    if region:
        params['region'] = region

    if stream_id:
        path = f"/stream/{stream_id}"
    else:
        path = "/playlist.m3u"

    query = urlencode(params)
    target_url = f"{base_url}{path}"
    if query:
        target_url = f"{target_url}?{query}"

    player_binary = vlc_path or shutil.which('vlc')
    if not player_binary:
        click.echo("VLC was not found on PATH. Use --vlc-path or run VLC manually:")
        click.echo(f"vlc {target_url}")
        return

    click.echo(f"Launching VLC with {target_url}")
    try:
        subprocess.Popen([player_binary, target_url])
    except OSError as exc:
        click.echo(f"Failed to start VLC: {exc}")

if __name__ == '__main__':
    main_cli()
