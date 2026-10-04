# filemind - Intelligent File Organization with AI

A modern daemon-based file organization system that automatically classifies, processes, and organizes files based on their content and type.

## Requirements

The authoritative product and operational requirements are maintained separately in [REQUIREMENTS.md](REQUIREMENTS.md). This README is the guide for installation, configuration, and use.

## Architecture

The required processing flow and integrations are defined in [REQUIREMENTS.md](REQUIREMENTS.md).

## Installation

### Prerequisites
- Python 3.9+
- pip or poetry
- [Ollama](https://ollama.com) with a vision model for AI naming (optional):
  ```bash
  ollama pull llava:7b
  ```

### Setup

1. **Clone the repository**
```bash
git clone https://github.com/yourusername/filemind.git
cd filemind
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Configure filemind**
```bash
cp config.yaml config.yaml.example
nano config.yaml  # Edit with your settings
```

4. **Create input directory**
```bash
mkdir input
```

### Install as a systemd Service (Linux Server)

filemind is designed to run as a daemon that starts with the server.
The repository ships a ready-to-use systemd unit and installer:

```bash
sudo ./deploy/install.sh
```

The installer:
1. creates the system user `filemind`,
2. copies the project to `/opt/filemind` and creates a virtualenv there,
3. installs the configuration to `/etc/filemind/config.yaml` (existing config is kept),
4. installs and enables the systemd unit `filemind` (autostart on boot).

Useful commands:

```bash
journalctl -u filemind -f          # follow logs
systemctl status filemind          # service status
```

The unit file lives in [deploy/filemind.service](deploy/filemind.service).
Adjust `ReadWritePaths` there if your storage paths differ from the defaults.

## Configuration

Edit `config.yaml`:

```yaml
# filemind - Intelligent File Organization with AI
# Configuration File

# Target language for generated file and folder names ("de" or "en")
# Affects AI naming, fallback name prefixes and reverse-geocoded
# country/city folders. Example: de -> "Deutschland", en -> "Germany"
language: "en"

# Logging System
logging:
  log_dir: ".filemind/logs"           # Directory for log files
  level: "INFO"                        # Root log level (DEBUG, INFO, WARNING, ERROR)
  retention_days: 7                    # Keep log files for 7 days
  console_enabled: true                # Print to console
  console_level: "WARNING"             # Only print warnings and errors to console

# Performance Diagnostics
performance:
  enabled: true                        # Write machine-readable stage metrics
  max_log_mb: 10                       # Rotate performance.jsonl at this size
  backup_count: 3                      # Number of rotated performance logs to keep

# Daemon Configuration
daemon:
  input_directories:                   # Directories to watch for new files
    - path: "/media/storage_main/syncthing"
      action: "copy"
    - path: "/media/storage_main/altes_backup"
      action: "move"
  poll_interval: 3600                  # Check for new files every 3600 seconds (1 hour)
  reprocess_processed: false           # Reprocess files already stored in the destination directories once at startup

# Storage Configuration
storage:
  base_media_path: "/media/storage_media"                  # Base path for organized media files
  base_documents_path: "/media/storage_main/scanner"       # Path where to move the documents
  max_files_per_folder: 3000                               # Maximum files per folder
  reinit_hash_store: false                                 # Whether to reinitialize the hash store on startup (WARNING: This will cause all files to be reprocessed)
  hash_db_path: ".filemind/hash_store.db"                  # SQLite database for SHA-256 deduplication

# AI Naming (Ollama)
ai:
  enabled: true                        # Enable AI-powered smart naming for photos
  provider: "ollama"                   # Currently only "ollama" is supported
  model: "llava:7b"                    # Vision model used to describe images and derive names
  url: "http://localhost:11434"        # Base URL of the local Ollama server
  timeout: 300                         # Request timeout in seconds (CPU inference incl. model load can take minutes)
  max_image_size: 1024                 # Downscale photos to this size (longest edge, px) before sending to the model (in memory only - the original file is never modified)
  num_ctx: 8192                        # Context window in tokens. The Ollama server default (4096) is too small for vision models with dynamic resolution - image tokens get truncated and requests fail. Larger values cost more RAM.

# Classification Rules
classification:
  # Image extensions (REAL_IMAGE)
  image_extensions:
    - ".jpg"
    - ".jpeg"
    - ".png"
    - ".gif"
    - ".bmp"
    - ".webp"
    - ".tiff"
    - ".heic"
    - ".nef"

  # Text document extensions (TEXT_DOCUMENT)
  text_document_extensions:
    - ".pdf"
    - ".docx"
    - ".doc"
    - ".odt"
    - ".xlsx"
  The script stops `filemind`, uploads the code and local `config.yaml`, updates
    - ".pptx"
    - ".txt"
    - ".rtf"

  # Video extensions (VIDEO)
  video_extensions:
    - ".mp4"
    - ".avi"
    - ".mkv"
  Download the active and rotated application logs (`filemind.log*`) and
  logs (`filemind.log*`) and extract them locally:
    - ".flv"
    - ".webm"
    - ".m4v"

  # Audio extensions (AUDIO)
  The default remote log directory is `/opt/filemind/.filemind/logs`, based on
    - ".mp3"
    - ".wav"
    - ".flac"
    - ".aac"
    - ".ogg"
    - ".wma"
    - ".m4a"
    - ".aiff"

  # Archive extensions (ARCHIVES)
  archive_extensions:
    - ".zip"
    - ".rar"
    - ".7z"
    - ".tar"
    - ".gz"
    - ".bz2"
    - ".xz"
```

## Usage

### Run the Daemon

```bash
# Start filemind daemon in foreground
python -m filemind.main

# With debug logging
python -m filemind.main --log-level DEBUG

# With custom input directory
python -m filemind.main --input-dir /path/to/watch

# Rebuild the persistent duplicate index before starting
python -m filemind.main --reinit-hash-store

# Reprocess files already stored in the configured destinations once
python -m filemind.main --reprocess-processed
```

### Remote Update and Logs

The secured updater stages an isolated release and asks the root-owned server
wrapper to switch releases. It replaces `/etc/filemind/config.yaml` with the
bundled local `config.yaml`; the hash database, logs, storage, and previous
releases are preserved. After the one-time server setup below, a manual update is:

```powershell
.\deploy\update-server.ps1
```

Download active and rotated application logs (`filemind.log*`) and performance
logs (`performance.jsonl*`):

```powershell
.\deploy\download-logs.ps1
```

The default log directory is `/opt/filemind/.filemind/logs`. The SSH account
already has read access in the tested setup, so the downloader uses no sudo.
Both scripts default to SSH alias `fritzleserver`. Logs can include personal
filenames and paths; do not share them without considering their contents.

### One-Time Server Setup

The root installer migrates the existing service to `/opt/filemind/current`,
preserves `/opt/filemind/.filemind` and `/etc/filemind/config.yaml`, installs
ExifTool if needed, creates a non-login build user, and adds a `sudoers` rule
that permits only the validated release wrapper. It briefly stops/restarts the
service but does not replace the active config or modify `.filemind` data. Run
these commands from the repository in PowerShell:

```powershell
scp.exe -o BatchMode=yes .\deploy\install-auto-deploy.sh .\deploy\filemind-deploy-wrapper.sh .\deploy\deploy_archive.py .\deploy\filemind.service fritzleserver:/tmp/
ssh.exe -tt fritzleserver "sudo bash /tmp/install-auto-deploy.sh"
```

The second command asks for the server sudo password in the terminal. It is used
only for this one-time installation and is neither saved nor given to Copilot.
Afterward, verify the wrapper without changing server state:

```powershell
ssh.exe -o BatchMode=yes fritzleserver "sudo -n /usr/local/sbin/filemind-deploy --check"
```

### Daily Audit Runner

`deploy/run-daily-audit.ps1` downloads recent logs, audits them in a persistent
isolated Git worktree, runs the complete test suite, and requests further repairs
after each test failure. Existing tests cannot be changed; new
`tests/test_*.py` files are allowed. It deploys only after all tests pass and
only when the runtime payload differs from the last successful deployment.
Changes are not committed or pushed. Reports and the worktree live under
`%LOCALAPPDATA%\filemind\daily-audit`.

Install the standalone Copilot CLI and authenticate once. The runner recognizes
the per-user binary at `%LOCALAPPDATA%\Programs\CopilotCLI\copilot.exe`:

```powershell
& "$env:LOCALAPPDATA\Programs\CopilotCLI\copilot.exe" login
```

Set the additional Copilot usage budget to `$0`; if included credits run out,
the run stops without a paid fallback. The runner sends recent redacted log
excerpts and test failures to Copilot, so register it only if you accept that
upload. Redaction is best-effort. The agent has no shell or MCP tools; the
outer runner executes tests and invokes the deploy script only after success.

Before the first run, commit the reviewed repository changes so the main checkout
is clean. Then perform a manual audit:

```powershell
pwsh -NoProfile -File .\deploy\run-daily-audit.ps1 -AllowLogUpload
```

After the one-time server setup, run the first full test-and-deploy pass manually:

```powershell
pwsh -NoProfile -File .\deploy\run-daily-audit.ps1 -AllowLogUpload -EnableDeploy
```

The runner checks the wrapper before uploading logs to Copilot. It deploys only
after a green full suite and only when the runtime payload hash changed. Then
register the daily 02:00 task with automatic deployment enabled:

```powershell
pwsh -NoProfile -File .\deploy\register-daily-audit.ps1 -EnableDeploy
```

The installer prompts for the Windows task account; Windows Task Scheduler
stores that account, not the server sudo password. Use the same account that has
Copilot CLI sign-in and the SSH key. Run the registered task once manually to
verify its non-interactive credentials and report output.

### File Classification

Required file categories and their handling are specified in [REQUIREMENTS.md](REQUIREMENTS.md). Extension lists are configurable in `config.yaml`.

### Output Structure

Files are organized in the following structure:

```
base_media_path/
├── 2026/
│   ├── Deutschland/
│   │   └── Hodenhagen/               # Photos with GPS: Country/City
│   │       └── 2026-06-10_nashorn_grasen_wiese.jpg
│   └── 2026-06-10_audio_xyz.aac      # Media without location: directly in year folder
├── 2026_1/                           # Overflow when a target folder exceeds
│   └── Deutschland/Hodenhagen/       # max_files_per_folder (direct files only)
└── ...

base_documents_path/                  # Documents are stored flat
├── scan001.pdf
└── ...
```

The `max_files_per_folder` limit (default 3000) counts only files lying directly
in the target folder - files in subfolders don't count. When a target folder is
full, storage overflows to the next year suffix folder (`2026` → `2026_1` → ...).

## Module Overview

### Core Modules

- **`classification.classifier`**: File type detection and classification
- **`routing.router`**: Main routing logic, orchestrates all processing (incl. directory structure)
- **`storage.hash_store`**: SHA-256 deduplication with SQLite persistence

### Integration Modules

- **`integrations.ocr`**: OCR interface (Stub, extensible)
- **`integrations.ai_naming`**: Smart file naming (Stub, LLM-ready)
- **`integrations.metadata_extractor`**: Metadata extraction for various file types

### System Modules

## Logging
.filemind/logs/
├── filemind.log       # Current log file
```

Log format:
## Performance Diagnostics

When enabled (the default), filemind writes hardware details and structured stage measurements to `.filemind/logs/performance.jsonl` (or the configured `logging.log_dir`). The file rotates at `performance.max_log_mb`; up to `performance.backup_count` older files are kept. Records include run/file correlation IDs, a sanitized snapshot of effective configuration, wall and process CPU time, and process RSS changes. Paths, filenames, file contents, AI prompts, and secrets are excluded. Long reprocessing passes emit periodic path-free counters and a final summary. Share this JSONL file and its rotations to compare OCR, hashing, AI, metadata, storage, and daemon timings.

## Development

### Run Tests

```bash
pytest tests/
```

### Check Code Style

```bash
black filemind/
pylint filemind/
```

filemind/
├── config.yaml                   # Configuration file
├── main.py                       # Daemon entry point
│   └── classifier.py             # File type classification
├── core/
│   └── models.py                 # Data models
│   ├── ai_naming.py              # Smart naming
│   └── metadata_extractor.py     # Metadata extraction
├── logging/
│   └── logger.py                 # Logging setup
├── routing/
│   └── router.py                 # Main routing logic
├── storage/
│   └── hash_store.py             # Deduplication
└── tests/
    └── test_*.py                 # Unit tests
```

## Troubleshooting

### "Duplicate not recognized"
- Check `.filemind/hash_store.db` exists
- Clear cache: Delete `.filemind/` directory
- Rebuild hash index by reprocessing files

### "Files not being processed"
- Check input directory path is correct
- Verify permissions: `chmod 755 input`
- Check logs in `.filemind/logs/filemind.log`
- Increase `poll_interval` in config if system is slow

### "Import errors on startup"
- Ensure you're running from the project directory
- Reinstall dependencies: `pip install --force-reinstall -r requirements.txt`
- Check Python version: `python --version` (must be 3.9+)

## Performance Considerations

- **Poll Interval**: Lower = faster detection, higher = less CPU
- **Max Files per Folder**: 3000 is safe for most systems; counts only direct files per folder, adjust based on your needs
- **Logging Level**: Set to WARNING in production to reduce I/O
- **Hash Store**: Uses SQLite with WAL mode for concurrent access

## Security

Security requirements for integrations and local data are maintained in [REQUIREMENTS.md](REQUIREMENTS.md).

## License

MIT License - See LICENSE file for details

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Commit with clear messages
4. Submit a pull request

## Support

- 📖 [Documentation](https://github.com/yourusername/filemind/wiki)
- 💬 [Discussions](https://github.com/yourusername/filemind/discussions)
- 🐛 [Issue Tracker](https://github.com/yourusername/filemind/issues)

---

Made with ❤️ for intelligent file organization
- Zielordner:
  - Bilder → Pictures
  - Videos → Videos
  - Sonstiges → Other
- Rollierende Log-Datei (7 Tage, konfigurierbar).
- Viele Tests, saubere Architektur, klare Module.
