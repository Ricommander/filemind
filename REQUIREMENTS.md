# filemind Requirements

This document is the authoritative source for filemind's product and operational requirements. The README is for installation, configuration, and usage guidance; it may link here but must not maintain a separate normative feature list.

Existing written requirements take precedence over additional behavior inferred only from the current implementation. If implementation or tests disagree with a requirement, record the discrepancy and resolve it deliberately rather than silently changing the requirement.

## Product Requirements

### File Intake and Classification

- Run as a long-lived daemon that polls configured input directories for files, including files in nested directories.
- Wait until a file's modification time is stable between observations before processing it, to avoid processing files while they are still being written.
- Support per-input-directory `move` and `copy` actions. Copying must leave the source file in place.
- Classify files into real images, document images, text documents, videos, audio, archives, and other files. Configured file extensions determine the type; image contents must not override a conflicting extension.
- For configured image extensions, distinguish document images from photos using OCR word count and image colorfulness. Colorful images with text remain photos. When OCR text is below the configured threshold, scan-related filename hints and TIFF extensions may be used as fallbacks. Both thresholds are configurable.
- Log every image classification decision at INFO level with the actual OCR word count, configured word threshold, actual colorfulness when measured, configured colorfulness threshold, final result, and a human-readable reason. If colorfulness is not measured, log that fact and explain which fallback determined the result.
- Provide OCR for document images.

### Naming and Metadata

- For real photos, AI naming first generates a short image description and then derives a filename stem from that description.
- Support German and English generated names and location folder names, selected through the top-level `language` configuration.
- If Ollama is unavailable, use a deterministic file-type-prefixed fallback based on the original filename. If AI naming is disabled, continue with metadata-based fallback naming.
- Allow `OLLAMA_MODEL` and `OLLAMA_URL` environment variables to override the configured Ollama model and URL.
- Downscale images for AI requests in memory only; never modify the original image.
- AI naming is optional and must be configurable independently of the rest of file processing.
- Prefer a valid EXIF `DateTimeOriginal` for generated dates. Otherwise use the oldest available modification or creation timestamp, with the current date as the final fallback.
- When image GPS metadata is present, reverse-geocode it to country and city names for storage folders. If a location cannot be determined, do not invent a location folder from raw coordinates.
- Analyze real-photo metadata with ExifTool, including EXIF, XMP, IPTC, and supported RAW formats, before attempting visual location inference. Do not scan non-photo files for image-location metadata. GPS coordinates take precedence and are reverse-geocoded. If coordinates are absent, ask the configured vision model to infer country and city independently; accept each field only at confidence >= 95%, and report both confidence values in application logs. Never infer a location for non-photo file types.
- Install ExifTool (`libimage-exiftool-perl` on supported Debian/Ubuntu deployments) so full metadata and embedded RAW previews can be read. If it is unavailable, retain the Pillow EXIF fallback and report the reduced coverage in debug logs.
- Extract metadata for videos, audio, archives, and other supported files and use it, where available, to produce useful names.

### Deduplication and Storage

- Detect binary duplicates using SHA-256 and a persistent SQLite hash index. Skip duplicates; do not automatically delete them.
- Allow a one-time reprocessing pass over files already present in configured storage destinations, requested by daemon configuration or command-line option. This pass bypasses duplicate skipping so updated processing and naming can take effect; normal duplicate handling resumes afterward.
- Remove empty subdirectories under configured input and storage directories after scans or reprocessing, but preserve the configured roots and any non-empty directories.
- Store documents flat under the configured document destination. Store media in year-based folders, with country and city subfolders when location data is available.
- Prefix generated media filenames with the selected file date and preserve the original file extension.
- Avoid overwriting an existing destination filename by appending a numeric suffix such as `_1` or `_2`.
- Enforce `storage.max_files_per_folder` (default 3000) against direct files only. When a target folder is full, use the next year-suffix root (`YYYY_1`, `YYYY_2`, ...); files in subfolders do not count toward the limit.

### Existing Documented Integration Requirement

- A previously documented requirement specifies optional Paperless NGX handling for documents, following OCR where applicable. This requirement remains authoritative if it overlaps with other feature descriptions and is not a statement of current implementation status. Store Paperless API credentials securely, review classified files before upload, and use HTTPS in production.
- Restrict permissions on `.filemind/hash_store.db`, which contains file hashes.

## Operational and Quality Requirements

- Keep daemon processing, shared state, logging, and the hash store safe for concurrent access; continue processing other files when an individual file or optional integration fails.
- Provide configurable logging to rotating files, optional console output, log levels, and retention.
- Provide configurable performance diagnostics, enabled by default, in bounded machine-readable JSONL logs. Include hardware/software context, a sanitized snapshot of effective non-secret configuration, per-file and per-stage wall time, process CPU time, process RSS changes, file size/type, and success/failure status without recording file paths, names, contents, prompts, or secrets.
- Correlate all measurements for one daemon invocation and one file, and measure the daemon poll cycle plus expensive processing stages including classification/OCR, hashing, AI requests, geocoding, and storage.
- Emit periodic, path-free progress counters and a final summary for long reprocessing passes, including attempted, completed, failed, missing, and remaining files.
- Read application settings through the central configuration layer. Support a configurable polling interval, input paths and actions, storage paths and limits, classification extensions and thresholds, AI options, and hash-store reinitialization.
- Provide command-line options for log level, config path, input directory override, and hash-store reinitialization.
- Support installation as a systemd service on Linux, including startup on boot.
- Provide Windows PowerShell scripts to deploy runtime updates over SSH, explicitly replacing the active server configuration with the bundled local `config.yaml` while preserving runtime data, and to retrieve active and rotated application and performance logs.
- Support Python 3.9 and newer.
- Maintain the application version as Semantic Versioning in `filemind.__version__`; increment it once for every implementation change set (patch for fixes/diagnostics/configuration, minor for backward-compatible features, major for breaking changes). Log the active version at daemon startup and in performance `run_start` records.
- Use temporary directories or curated fixtures in tests. Tests must not depend on ignored local runtime data under `tests/documents/` or `tests/media/`.