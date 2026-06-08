Ich durchsuchen das Projekt nach typischen Stub-Markern: `TODO`, `FIXME`, `raise NotImplementedError`, `pass`, `...`.

Searched for regex `raise NotImplementedError|TODO|FIXME|#\s*stub|pass\s*$|\.\.\.|raise NotImplementedError\(|NotImplementedError` (`**/*.py`), 20 results

Ich öffne die betroffenen Dateien, um die gefundenen Stub-/TODO-Stellen zu prüfen.

Read [](file:///c%3A/Users/ricom/git/filemind/integrations/metadata_extractor.py#1-1), lines 1 to 320

Jetzt öffne ich ocr.py, client.py, router.py, hash_store.py und structure_manager.py zur Kontrolle.

Read [](file:///c%3A/Users/ricom/git/filemind/integrations/ocr.py)

Read [](file:///c%3A/Users/ricom/git/filemind/paperless/client.py#1-1), lines 1 to 300

Read [](file:///c%3A/Users/ricom/git/filemind/paperless/client.py#300-300), lines 300 to 477

Read [](file:///c%3A/Users/ricom/git/filemind/routing/router.py#1-1), lines 1 to 260

Read [](file:///c%3A/Users/ricom/git/filemind/storage/hash_store.py#1-1), lines 1 to 400

Read [](file:///c%3A/Users/ricom/git/filemind/storage/hash_store.py#400-400), lines 400 to 468

Read [](file:///c%3A/Users/ricom/git/filemind/storage/structure_manager.py)

Created 3 todos

Ich suche projektweit nach dem Begriff "Stub" für weitere Treffer.

Searched for text `Stub` (`**/*.py`), 20 results

Read [](file:///c%3A/Users/ricom/git/filemind/classification/classifier.py)

Searched for text `TODO` (`**/*.py`), 1 result

Read [](file:///c%3A/Users/ricom/git/filemind/tests/test_classifier.py)

Ich habe den Code nach Stub-/TODO-Markern durchsucht und die Stellen gefunden.

- **metadata_extractor.py**: Stub-Implementierungen für Metadaten-Extraktion (`extract_metadata_name`, `_extract_video_metadata_name`, `_extract_audio_metadata_name`, `_extract_archive_metadata_name`, `_extract_generic_metadata_name`). Details: metadata_extractor.py  
- **ocr.py**: komplette Stub-Funktion `ocr_to_text` (gibt Platzhalter-String zurück). Details: ocr.py  
- **classifier.py**: Stub-Heuristik `detect_text_in_image` (Platzhalter statt echter OCR). Details: classifier.py  
- **router.py**: Nutzung von Stub-Namen (Kommentar „Generiere neuen Namen (Stub)“) und ein `# TODO: Jahr aus config` (harte Kodierung `2026`). Details: router.py  
- **test_classifier.py**: Test erwartet das Stub-Verhalten von `detect_text_in_image` (prüft die Platzhalter-Rückgabe). Details: test_classifier.py

Möchtest du, dass ich an einer der Stellen eine konkrete Implementierung vorschlage oder einen Ticket-Liste mit Priorität erstelle?

Completed: *Review files for stub markers* (2/3)