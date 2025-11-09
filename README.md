# ⚡ Amps – Advanced Media Playlist Server

**Amps** is a Flask-based server that dynamically generates and serves `.m3u` playlists, relays or transcodes media streams using **FFmpeg**, and allows for easy configuration through a `config.yaml` file.

It's designed to be a developer-friendly, modular, and robust solution for personal or small-scale media streaming needs.



## Features

- **Dynamic M3U Playlists:** Serves a `/playlist.m3u` file compatible with most media players (VLC, Kodi, etc.) complete with channel names, logos, and custom metadata.
- **FFmpeg Engine:** Relays streams (`copy` codec) or transcodes them on-the-fly to different bitrates, resolutions, or formats.
- **YAML Configuration:** All streams and FFmpeg profiles are defined in a simple, human-readable `config.yaml`.
- **REST API:** A simple API to list, add, update, and delete streams in-memory without restarting the server.
- **Upcoming Programming:** Attach next-up schedules to channels and retrieve them through the API for live program feeds.
- **Custom FFmpeg Pipelines:** Override the FFmpeg command per channel when you need full control over how the stream is produced.
- **Token Authentication:** Secure your streams with a shared token, passed via headers or URL parameters.
- **Robust Process Management:** Automatically restarts broken streams on request and gracefully cleans up FFmpeg processes on shutdown.
- **Containerized:** Includes a `Dockerfile` for easy, production-ready deployment.
- **CLI Interface:** Manage the server with simple commands like `amps serve` and `amps list`.

## Architecture

Amps consists of several key components:

1.  **Flask Server (`server.py`):** The web core that handles HTTP requests.
    -   `/playlist.m3u`: Generates the playlist from the current stream configuration.
    -   `/stream/<id>`: Serves the video content. This is a streaming endpoint that holds a connection open.
    -   `/api/streams`: Provides CRUD operations for in-memory stream management.
    -   `/metrics`: Exposes basic server metrics.
2.  **FFmpeg Wrapper (`ffmpeg_utils.py`):** Manages all FFmpeg subprocesses. It starts, stops, and monitors processes, ensuring that a stream is available when requested. It uses `ffmpeg-python` to build FFmpeg commands safely.
3.  **Configuration (`config_loader.py`):** Parses and validates `config.yaml` at startup.
4.  **CLI (`cli.py`):** Provides command-line entry points using `click`. When `server.debug` is `false`, it launches a production-ready `gunicorn` server.

## Setup and Installation

### 1. Prerequisites

-   Python 3.7+
-   `ffmpeg` installed and available in your system's PATH.
-   [Docker](https://www.docker.com/get-started) (for containerized deployment).

### 2. Local Installation (for development)

1.  **Clone the repository:**
    ```bash
    git clone <repository_url>
    cd amps-project
    ```
2.  **Create a virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```
3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Configure `config.yaml`:**
    Modify the `config.yaml` file to add your streams and set a secure auth token. Channels can now specify additional metadata such as logos, alternate guide names, upcoming programs, and even bespoke FFmpeg commands.

5.  **Run the server:**
    ```bash
    python -m amps serve
    ```

### 3. Docker Deployment (recommended for production)

1.  **Configure `config.yaml`:**
    Make sure your `config.yaml` is ready.

2.  **Build the Docker image:**
    ```bash
    docker build -t amps-server .
    ```

3.  **Run the container:**
    ```bash
    docker run -d -p 5000:5000 --name amps -v $(pwd)/config.yaml:/app/config.yaml --restart unless-stopped amps-server
    ```
    This command:
    -   Runs the container in detached mode (`-d`).
    -   Maps port 5000 on your host to port 5000 in the container (`-p`).
    -   Mounts your local `config.yaml` into the container, allowing you to change it without rebuilding the image (`-v`).
    -   Ensures the container automatically restarts if it stops (`--restart unless-stopped`).

## Usage

### Accessing the Playlist

Open this URL in a browser or media player like VLC. You must provide the token.

**URL Format:**
`http://<server_ip>:5000/playlist.m3u?token=<your_token>`

**cURL Example:**
```bash
curl -L "http://localhost:5000/playlist.m3u?token=changeme123"
```

The playlist includes extended metadata when available:

- `tvg-name`, `tvg-logo`, `group-title`, and `channel-number` attributes.
- `#EXTREM:AMP-NEXT` lines for the next scheduled program (title, start time, description).
- `#EXTREM:AMP-PROGRAM-FEED` linking to external schedule feeds.
- `#EXTREM:AMP-DESCRIPTION` containing rich channel descriptions.

Players that ignore custom tags will safely skip them, while companion applications can parse them for richer experiences.

## Stream Configuration Reference

Each entry in the `streams` list accepts the following keys:

| Key | Required | Description |
| --- | --- | --- |
| `id` | ✅ | Unique integer identifier for the stream. |
| `name` | ✅ | Display name for the channel. |
| `source` | ✅ | Input URL or file path passed to FFmpeg. |
| `ffmpeg_profile` | ✅* | Name of a profile defined under `ffmpeg_profiles`. Required unless `custom_ffmpeg` is supplied. |
| `custom_ffmpeg` | ✅* | Optional override to launch a bespoke FFmpeg command instead of a profile. Accepts a string or mapping with `command`, `shell`, `cwd`, and `env` fields. The `{source}`, `{id}`, and `{name}` placeholders are expanded. |
| `tvg_name` | | Alternate guide name used in playlist metadata. |
| `logo` | | URL to the channel logo. |
| `group` | | Logical group title for player UIs. |
| `channel_number` | | Numeric channel identifier for compatible players. |
| `description` | | Long-form description inserted as a custom playlist tag. |
| `program_feed` | | URL to an external schedule feed for companion apps. |
| `next_programs` | | List of upcoming program objects with at least a `title`, plus optional `start` and `description` fields. |

> ℹ️ Provide either `ffmpeg_profile` or `custom_ffmpeg`. When both are supplied, the profile is still available for reference but the custom command takes precedence.

### Managing Upcoming Programs via the API

Use the new endpoint to fetch or replace the upcoming schedule for a channel without reloading configuration files:

```
GET  /api/streams/<id>/programs
PUT  /api/streams/<id>/programs
```

The `PUT` body should be a JSON array of program objects. Each object must contain a `title` and can optionally include `start` and `description` fields.
