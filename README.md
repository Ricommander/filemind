# filemind - Intelligent File Organization with AI

A modern daemon-based file organization system that automatically classifies, processes, and organizes files based on their content and type.

## Features

- 🤖 **Intelligent Classification**: Automatically detects file types (images, documents, videos, audio, archives)
- 📄 **OCR Support**: Extract text from document images (Stub, expandable)
- 📋 **Paperless NGX Integration**: Upload documents directly to your Paperless instance
- 🔍 **Binary Deduplication**: Detect and prevent duplicate files using SHA-256 hashing
- 🏷️ **Smart Naming**: AI-powered file naming (configurable, OpenAI/Hugging Face ready)
- 📁 **Organized Storage**: Year-based directory structures (YYYY_NN format, max 3000 files per folder)
- 🧵 **Thread-Safe**: Concurrent processing with proper locking mechanisms
- 📊 **Comprehensive Logging**: Rotating file handlers with configurable retention

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
    ├─ Documents → OCR (optional) → Paperless NGX
    ├─ Images → Smart Naming (optional) → Storage
    ├─ Videos/Audio/Archives → Metadata Extraction → Storage
    └─ Others → Storage
    ↓
[5] Organized Output (YYYY_NN folders)
```

## Installation

### Prerequisites
- Python 3.9+
- pip or poetry

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

## Configuration

Edit `config.yaml`:

```yaml
# Logging
logging:
  log_dir: ".filemind/logs"
  level: "INFO"
  retention_days: 7

# Daemon
daemon:
  input_directories:
    - "input"
  poll_interval: 5  # seconds

# Storage
storage:
  base_path: "output"
  max_files_per_subfolder: 3000

# Paperless NGX (optional)
paperless:
  url: "http://localhost:8000"
  token: "YOUR_API_TOKEN"
  enabled: false

# OCR (optional, Stub)
ocr:
  enabled: false
  provider: "stub"  # "tesseract", "easyocr", etc.

# AI Naming (optional, Stub)
ai:
  enabled: false
  provider: "stub"  # "openai", "huggingface", etc.
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

| Type | Extensions | Behavior |
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
│   ├── 2026_01/  (up to 3000 files)
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

- **`paperless.client`**: Paperless NGX API integration
- **`logging.logger`**: Thread-safe rotating file logging
- **`main`**: Daemon entry point and orchestration

## Extending filemind

### Add OCR Support

Replace the stub in `integrations/ocr.py`:

```python
def ocr_to_text(path: Path) -> str:
    # Implement with Tesseract, EasyOCR, PaddleOCR, etc.
    import pytesseract
    return pytesseract.image_to_string(str(path))
```

### Add AI Naming

Replace the stub in `integrations/ai_naming.py`:

```python
def generate_smart_name(path: Path) -> str:
    from openai import OpenAI
    client = OpenAI()
    
    # Analyze file and generate name using GPT-4
    response = client.chat.completions.create(...)
    return response.choices[0].message.content
```

### Add Custom File Handlers

Extend `routing/router.py`:

```python
elif file_type == FileType.CUSTOM:
    logger.debug("Processing custom type")
    # Add your custom logic here
```

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
├── paperless/
│   └── client.py                 # Paperless NGX API
├── routing/
│   └── router.py                 # Main routing logic
├── storage/
│   ├── hash_store.py             # Deduplication
│   └── structure_manager.py      # Directory structure
└── tests/
    └── test_*.py                 # Unit tests
```

## Troubleshooting

### "Paperless connection refused"
- Verify Paperless NGX is running: `curl http://localhost:8000/api/`
- Check API token in `config.yaml`
- Enable Paperless in config if not already

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
