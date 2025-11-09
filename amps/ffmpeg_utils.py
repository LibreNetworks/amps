# amps/ffmpeg_utils.py

import ffmpeg
import logging
import subprocess
import threading
import atexit
import shlex
from typing import Dict, Optional, Tuple, Union, List

# Global dictionary to hold running FFmpeg processes and associated data
# Structure: { stream_id: {'process': Popen_object, 'lock': Lock_object} }
RUNNING_PROCESSES: Dict[int, Dict] = {}


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
                # Build ffmpeg-python command
                input_stream = ffmpeg.input(stream_config['source'])
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
