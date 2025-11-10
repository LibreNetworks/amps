# amps/ffmpeg_utils.py

import ffmpeg
import logging
import subprocess
import threading
import atexit
import shlex
from typing import Dict, Optional, Tuple, Union, List, Any

try:
    import yt_dlp
except ImportError:  # pragma: no cover - dependency should be installed, but guard just in case
    yt_dlp = None

# Global dictionary to hold running FFmpeg processes and associated data
# Structure: { stream_id: {'process': Popen_object, 'lock': Lock_object} }
RUNNING_PROCESSES: Dict[int, Dict] = {}


def _resolve_stream_source(stream_config: dict) -> Tuple[Optional[str], Dict[str, Any]]:
    """Resolves the input source for FFmpeg, optionally using yt-dlp."""

    source = stream_config.get('source')
    if not source:
        logging.error("Stream '%s' is missing a source URL.", stream_config.get('name', stream_config.get('id')))
        return None, {}

    # Normalised configuration for yt-dlp usage. We support either the legacy
    # `use_yt_dlp` boolean or the richer `source_handler` mapping.
    handler_conf = stream_config.get('source_handler') or {}

    if stream_config.get('use_yt_dlp') and not handler_conf:
        handler_conf = {
            'type': 'yt_dlp',
            'format': stream_config.get('yt_dlp_format')
        }

    handler_type = (handler_conf.get('type') or '').lower()

    if handler_type != 'yt_dlp':
        return source, {}

    if yt_dlp is None:
        logging.error(
            "yt-dlp support requested for stream '%s', but the package is not available.",
            stream_config.get('name', stream_config.get('id')),
        )
        return None, {}

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': handler_conf.get('format') or 'best',
        'noplaylist': True,
        'cachedir': False,
        'skip_download': True,
    }

    # Allow users to pass additional yt-dlp options via `options` mapping.
    extra_opts = handler_conf.get('options')
    if isinstance(extra_opts, dict):
        ydl_opts.update(extra_opts)

    stream_label = stream_config.get('name', stream_config.get('id'))

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(source, download=False)
    except Exception as exc:  # pragma: no cover - network errors/environment specific
        logging.error("yt-dlp failed to extract stream for '%s': %s", stream_label, exc)
        return None, {}

    if not info:
        logging.error("yt-dlp did not return stream information for '%s'.", stream_label)
        return None, {}

    if 'entries' in info:
        entries = info.get('entries') or []
        info = next((entry for entry in entries if entry), None)
        if not info:
            logging.error("yt-dlp returned an empty playlist for '%s'.", stream_label)
            return None, {}

    resolved = info.get('url') or info.get('manifest_url')
    if not resolved:
        logging.error("yt-dlp did not provide a playable URL for '%s'.", stream_label)
        return None, {}

    input_overrides: Dict[str, Any] = {}

    headers = info.get('http_headers')
    if headers:
        header_lines = ''.join(f"{key}: {value}\r\n" for key, value in headers.items())
        input_overrides['headers'] = header_lines

    protocol = info.get('protocol')
    if protocol and protocol.startswith('m3u8'):
        input_overrides.setdefault('protocol_whitelist', 'file,http,https,tcp,tls,crypto')

    return resolved, input_overrides


def _prepare_custom_ffmpeg_command(stream_config: dict) -> Optional[Tuple[Union[str, List[str]], bool, Optional[dict], Optional[str]]]:
    """Builds a custom FFmpeg command for a stream if configured."""

    custom_conf = stream_config.get('custom_ffmpeg')
    if not custom_conf:
        return None

    # Allow shorthand string for simple commands
    if isinstance(custom_conf, str):
        custom_conf = {'command': custom_conf}

    if not isinstance(custom_conf, dict):
        logging.error("custom_ffmpeg configuration must be a string or mapping.")
        return None

    command_template = custom_conf.get('command')
    if not command_template:
        logging.error("custom_ffmpeg configuration missing 'command' entry.")
        return None

    context = {
        'source': stream_config.get('source', ''),
        'id': stream_config.get('id'),
        'name': stream_config.get('name', ''),
    }

    shell = bool(custom_conf.get('shell', False))

    if isinstance(command_template, list):
        command = [str(arg).format(**context) for arg in command_template]
    elif isinstance(command_template, str):
        formatted = command_template.format(**context)
        command = formatted if shell else shlex.split(formatted)
    else:
        logging.error("custom_ffmpeg 'command' must be a string or list of arguments.")
        return None

    env = custom_conf.get('env')
    cwd = custom_conf.get('cwd')

    if env is not None and not isinstance(env, dict):
        logging.error("custom_ffmpeg 'env' must be a mapping of environment variables.")
        env = None

    return command, shell, env, cwd

def _log_stderr(stream_name: str, stderr_pipe):
    """
    Reads from a process's stderr pipe and logs each line for debugging.
    """
    for line in iter(stderr_pipe.readline, b''):
        logging.getLogger('ffmpeg').info(f"[{stream_name}] {line.decode('utf-8').strip()}")

def get_or_start_stream_process(stream_config: dict, ffmpeg_profile: dict) -> Optional[subprocess.Popen]:
    """
    Retrieves a running FFmpeg process for a stream or starts a new one.
    This function is thread-safe.
    """
    stream_id = stream_config['id']
    stream_name = stream_config.get('name', f"Stream {stream_id}")

    # Initialize stream entry if not present
    if stream_id not in RUNNING_PROCESSES:
        RUNNING_PROCESSES[stream_id] = {
            'process': None,
            'lock': threading.Lock()
        }

    with RUNNING_PROCESSES[stream_id]['lock']:
        proc_data = RUNNING_PROCESSES[stream_id]
        process = proc_data.get('process')

        # Check if process exists and is running
        if process and process.poll() is None:
            logging.info(f"Returning existing FFmpeg process for stream '{stream_name}' (PID: {process.pid})")
            return process

        # If process is dead or doesn't exist, start a new one
        logging.info(f"Starting new FFmpeg process for stream '{stream_name}'")
        try:
            custom_command = _prepare_custom_ffmpeg_command(stream_config)

            if custom_command:
                command, use_shell, env, cwd = custom_command
                logging.info(f"Launching custom FFmpeg command for '{stream_name}': {command}")
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=use_shell,
                    env=env,
                    cwd=cwd,
                )
                logging.info(f"Custom FFmpeg process started for '{stream_name}' with PID: {process.pid}")
            else:
                resolved_source, handler_options = _resolve_stream_source(stream_config)
                if not resolved_source:
                    logging.error(
                        "Could not resolve an input source for stream '%s'.", stream_name
                    )
                    return None

                input_kwargs: Dict[str, Any] = {}
                input_kwargs.update(handler_options)

                configured_options = stream_config.get('input_options') or {}
                if configured_options and not isinstance(configured_options, dict):
                    logging.error(
                        "Stream '%s' has non-mapping input_options; ignoring the value.",
                        stream_name,
                    )
                else:
                    input_kwargs.update(configured_options)

                input_args = stream_config.get('input_args') or []
                if input_args and not isinstance(input_args, list):
                    logging.error(
                        "Stream '%s' input_args must be a list of arguments; ignoring.",
                        stream_name,
                    )
                    input_args = []

                logging.debug(
                    "FFmpeg input for '%s': source=%s, args=%s, options=%s",
                    stream_name,
                    resolved_source,
                    input_args,
                    input_kwargs,
                )

                input_stream = ffmpeg.input(resolved_source, *input_args, **input_kwargs)
                output_stream = ffmpeg.output(input_stream, 'pipe:1', **ffmpeg_profile)

                process = output_stream.run_async(pipe_stdout=True, pipe_stderr=True)
                logging.info(f"FFmpeg process started for '{stream_name}' with PID: {process.pid}")

            # Start a thread to log stderr for this process
            stderr_thread = threading.Thread(
                target=_log_stderr,
                args=(stream_name, process.stderr),
                daemon=True
            )
            stderr_thread.start()

            proc_data['process'] = process
            return process

        except ffmpeg.Error as e:
            logging.error(f"FFmpeg error for stream '{stream_name}': {e.stderr.decode('utf-8')}")
            return None
        except Exception as e:
            logging.error(f"Failed to start FFmpeg for stream '{stream_name}': {e}")
            return None

def stop_stream_process(stream_id: int):
    """
    Stops a specific FFmpeg process if it is running.
    """
    if stream_id in RUNNING_PROCESSES:
        with RUNNING_PROCESSES[stream_id]['lock']:
            proc_data = RUNNING_PROCESSES.pop(stream_id)
            process = proc_data.get('process')
            if process and process.poll() is None:
                logging.warning(f"Terminating FFmpeg process for stream ID {stream_id} (PID: {process.pid})")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logging.error(f"FFmpeg process {process.pid} did not terminate gracefully, killing.")
                    process.kill()
                logging.info(f"Process for stream ID {stream_id} stopped.")

def cleanup_all_processes():
    """
    Cleans up all running FFmpeg processes on application exit.
    """
    logging.info("Shutting down all active FFmpeg streams...")
    stream_ids = list(RUNNING_PROCESSES.keys())
    for stream_id in stream_ids:
        stop_stream_process(stream_id)
    logging.info("Cleanup complete.")

# Register the cleanup function to be called on exit
atexit.register(cleanup_all_processes)
