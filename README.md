# filemind - Intelligent File Organization with AI

A modern daemon-based file organization system that automatically classifies, processes, and organizes files based on their content and type.

## Features

- 🤖 **Intelligent Classification**: Automatically detects file types (images, documents, videos, audio, archives)
- 📄 **OCR Support**: Extract text from document images
- 🔍 **Binary Deduplication**: Detect and prevent duplicate files using SHA-256 hashing
- 🏷️ **Smart Naming**: AI-powered file naming (first describes the image, then finds the best name based on the description of the image)
- 📁 **Organized Storage**: Year-based directory structures (YYYY_NN\Country\City format, max 3000 files per folder)
- 🧵 **Thread-Safe**: Concurrent processing with proper locking mechanisms
- 📊 **Comprehensive Logging**: Rotating file handlers with configurable retention
- **GPS reverse geocoding**: Finds the correct country and city of an image if GPS coordinates are present in the metadata.

## Architecture

```
Input Directory
    ↓
[1] File Monitoring (Daemon/Polling)
    ↓
[2] Classification (File Type Detection)
    ↓
[3] Deduplication Check (SHA-256 Hash)
    ↓
[4] Type-Specific Processing
    ├─ Documents → OCR → Paperless NGX
    ├─ Images → Smart Naming → Storage
    ├─ Videos/Audio/Archives → Metadata Extraction → Storage
    └─ Others → Storage
    ↓
[5] Organized Output (YYYY_NN\Country\City folders)
```

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
systemctl restart filemind         # restart after config changes
```

The unit file lives in [deploy/filemind.service](deploy/filemind.service).
Adjust `ReadWritePaths` there if your storage paths differ from the defaults.

## Configuration

Edit `config.yaml`:

```yaml
# filemind - Intelligent File Organization with AI
# Configuration File

# Logging System
logging:
  log_dir: ".filemind/logs"           # Directory for log files
  level: "INFO"                        # Root log level (DEBUG, INFO, WARNING, ERROR)
  retention_days: 7                    # Keep log files for 7 days
  console_enabled: true                # Print to console
  console_level: "WARNING"             # Only print warnings and errors to console

# Daemon Configuration
daemon:
  input_directories:                   # Directories to watch for new files
    - path: "/media/storage_main/syncthing"
      action: "copy"
    - path: "/media/storage_main/altes_backup"
      action: "move"
  poll_interval: 3600                  # Check for new files every 3600 seconds (1 hour)

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
  timeout: 120                         # Request timeout in seconds (CPU inference can be slow)

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

  # Text document extensions (TEXT_DOCUMENT)
  text_document_extensions:
    - ".pdf"
    - ".docx"
    - ".doc"
    - ".odt"
    - ".xlsx"
    - ".xls"
    - ".pptx"
    - ".txt"
    - ".rtf"

  # Video extensions (VIDEO)
  video_extensions:
    - ".mp4"
    - ".avi"
    - ".mkv"
    - ".mov"
    - ".wmv"
    - ".flv"
    - ".webm"
    - ".m4v"

  # Audio extensions (AUDIO)
  audio_extensions:
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
```

### File Classification

The system automatically classifies files into these categories:

| Type | Extensions | Behavior  |
|------|-----------|----------|
| **Real Image** | jpg, png, gif, etc. | Smart naming, deduplication, organized storage |
| **Document Image** | PDF (scanned), TIF | OCR (optional), Paperless (optional), storage |
| **Text Document** | PDF (native), docx, xlsx, txt, etc. | Paperless (optional), storage |
| **Video** | mp4, mkv, avi, webm, etc. | Metadata extraction, organized storage |
| **Audio** | mp3, wav, flac, aac, etc. | Metadata extraction, organized storage |
| **Archives** | zip, rar, 7z, tar, etc. | Metadata extraction, organized storage |
| **Other** | Everything else | Generic handling, organized storage |

### Output Structure

Files are organized in the following structure:

```
output/
├── Pictures/
│   ├── 2026_01/Deutschland/Hodenhagen  (up to 3000 files)
│   ├── 2026_02/
│   └── ...
├── Documents/
│   ├── 2026_01/
│   └── ...
├── Videos/
│   ├── 2026_01/
│   └── ...
├── Audio/
│   ├── 2026_01/
│   └── ...
├── Archives/
│   ├── 2026_01/
│   └── ...
└── Other/
    └── ...
```

## Module Overview

### Core Modules

- **`classification.classifier`**: File type detection and classification
- **`routing.router`**: Main routing logic, orchestrates all processing
- **`storage.structure_manager`**: Directory structure management (YYYY_NN folders)
- **`storage.hash_store`**: SHA-256 deduplication with SQLite persistence

### Integration Modules

- **`integrations.ocr`**: OCR interface (Stub, extensible)
- **`integrations.ai_naming`**: Smart file naming (Stub, LLM-ready)
- **`integrations.metadata_extractor`**: Metadata extraction for various file types

### System Modules

- **`logging.logger`**: Thread-safe rotating file logging
- **`main`**: Daemon entry point and orchestration

## Logging

Logs are stored in `.filemind/logs/`:

```
.filemind/logs/
├── filemind.log       # Current log file
├── filemind.log.1     # Yesterday's log
├── filemind.log.2     # Day before, etc.
└── ...                # (7 days by default)
```

Log format:
```
2026-05-25 12:34:56 - filemind.routing.router - INFO - Verarbeitung abgeschlossen: document.pdf
```

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

### Project Structure

```
filemind/
├── __init__.py
├── config.yaml                   # Configuration file
├── main.py                       # Daemon entry point
├── classification/
│   └── classifier.py             # File type classification
├── core/
│   └── models.py                 # Data models
├── integrations/
│   ├── ocr.py                    # OCR interface
│   ├── ai_naming.py              # Smart naming
│   └── metadata_extractor.py     # Metadata extraction
├── logging/
│   └── logger.py                 # Logging setup
├── routing/
│   └── router.py                 # Main routing logic
├── storage/
│   ├── hash_store.py             # Deduplication
│   └── structure_manager.py      # Directory structure
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
- **Max Files per Folder**: 3000 is safe for most systems; adjust based on your needs
- **Logging Level**: Set to WARNING in production to reduce I/O
- **Hash Store**: Uses SQLite with WAL mode for concurrent access

## Security Notes

- Store Paperless API token in environment variable or encrypted config
- Restrict `.filemind/hash_store.db` permissions (contains file hashes)
- Review classified files before Paperless upload
- Use HTTPS for Paperless connections in production

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
