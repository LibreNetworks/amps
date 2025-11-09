# amps/config_loader.py

import yaml
import logging
import copy

DEFAULT_CONFIG = {
    'server': {
        'host': '0.0.0.0',
        'port': 5000,
        'debug': False,
    },
    'auth': {
        'enabled': True,
        'token': 'changeme123',
    },
    'streams': [],
    'ffmpeg_profiles': {}
}

def load_config(config_path: str) -> dict:
    """
    Loads, validates, and provides defaults for the YAML configuration.
    """
    config = copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(config_path, 'r') as f:
            user_config = yaml.safe_load(f)
            if not user_config:
                logging.warning(f"Configuration file '{config_path}' is empty. Using defaults.")
                return config

            # Deep merge user config into default config
            if 'server' in user_config:
                config['server'].update(user_config['server'])
            if 'auth' in user_config:
                config['auth'].update(user_config['auth'])
            if 'streams' in user_config:
                config['streams'] = user_config['streams']
            if 'ffmpeg_profiles' in user_config:
                config['ffmpeg_profiles'] = user_config['ffmpeg_profiles']

        logging.info(f"Loaded configuration from '{config_path}'.")
        logging.info(f"Loaded {len(config['streams'])} streams and {len(config['ffmpeg_profiles'])} FFmpeg profiles.")

        # In-memory store for API modifications
        config['stream_map'] = {stream['id']: stream for stream in config['streams']}

        return config

    except FileNotFoundError:
        logging.error(f"Configuration file not found at '{config_path}'. Aborting.")
        exit(1)
    except yaml.YAMLError as e:
        logging.error(f"Error parsing YAML file '{config_path}': {e}. Aborting.")
        exit(1)
    except Exception as e:
        logging.error(f"An unexpected error occurred while loading config: {e}. Aborting.")
        exit(1)
