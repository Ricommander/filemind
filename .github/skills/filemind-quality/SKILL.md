---
name: filemind-quality
description: "Projekt-Skill für filemind: bei Implementierung, Änderung, Fehlerbehebung, Refactoring und Review Anforderungen klären, bestehende Dateiverträge erhalten sowie passende Tests und Qualitätschecks durchführen. Use for filemind coding, bug fixes, feature requests, refactoring, and code review."
user-invocable: true
---

# filemind: Anforderungen und Codequalität

## Zweck

Stelle bei jeder Coding-Anfrage für dieses Repository sicher, dass die tatsächliche Nutzeranforderung erfüllt wird, bestehendes filemind-Verhalten erhalten bleibt und die Änderung mit passenden Prüfungen abgesichert ist. Arbeite eng am betroffenen Codepfad und ändere keine unabhängigen Bereiche.

## Verbindliche Anforderungen

- Lies zu Beginn jeder Implementierungs-, Fehlerbehebungs-, Refactoring- und Review-Anfrage die zentrale [REQUIREMENTS.md](../../../REQUIREMENTS.md) und berücksichtige die für den betroffenen Bereich relevanten Anforderungen als Akzeptanzkriterien.
- `REQUIREMENTS.md` ist die maßgebliche Quelle für Produkt- und Betriebsanforderungen. Die README darf darauf verweisen und Installation, Konfiguration und Bedienung erläutern, ist aber keine zweite Quelle für normative Anforderungen.
- Bereits festgehaltene Anforderungen haben Vorrang vor Verhalten, das lediglich aus der aktuellen Implementierung abgeleitet wird. Eine ausdrückliche neue Nutzeranforderung kann bestehende Anforderungen ändern.
- Weichen Implementierung oder Tests von einer Anforderung ab, benenne die Abweichung und behandle sie nicht stillschweigend als neue Soll-Anforderung. Bei einer beabsichtigten Verhaltensänderung aktualisiere `REQUIREMENTS.md` zusammen mit den betroffenen Tests und Bedienungsdokumenten.

## Vorgehen

1. **Anforderung festlegen.** Lies zuerst `REQUIREMENTS.md`. Formuliere danach das gewünschte beobachtbare Verhalten und erkennbare Randfälle. Kläre nur dann nach, wenn eine Mehrdeutigkeit zu unterschiedlichen Implementierungen oder einem Datenverlustrisiko führen würde. Leite aus Anforderungen, Tests, Konfiguration und Aufrufern ab, was kompatibel bleiben muss.
2. **Lokalen Kontrollpfad prüfen.** Lies die zuständigen Projektanweisungen und nur die relevanten Module, Tests und Aufrufstellen. Finde heraus, wo das Verhalten tatsächlich entschieden wird. Benenne vor der Änderung eine überprüfbare Hypothese und einen gezielten Check, der sie widerlegen könnte.
3. **Klein und ursächlich ändern.** Behebe die Ursache statt Symptome. Halte öffentliche APIs, Konfigurationsschlüssel, Rückgabewerte und Dateiverträge stabil, außer die Anforderung verlangt ausdrücklich eine Änderung. Vermeide unnötige Abstraktionen und unabhängiges Aufräumen.
4. **Daten und Seiteneffekte schützen.** Betrachte Verschieben, Kopieren, Umbenennen, Löschen, Hash-Registrierung und Zielordnerwahl als kritische Verträge. Überschreibe oder entferne keine Nutzerdaten unbeabsichtigt. Behalte Fehlerbehandlung und sinnvolle Logs bei; behandle externe Dienste wie Ollama, OCR und Geocoding so, dass Ausfälle kontrolliert bleiben.
5. **Gezielt verifizieren.** Führe nach der ersten Änderung zuerst den billigsten aussagekräftigen Test für den betroffenen Pfad aus. Ergänze danach relevante Tests, Linting oder Typ-/Syntaxprüfungen. Bei Änderungen an gemeinsam genutzter Logik oder mehreren Komponenten führe die ganze Suite aus. Melde genau, was ausgeführt wurde und was nicht geprüft werden konnte.
6. **Abschluss abgleichen.** Prüfe, dass jede Anforderung durch Code oder Test abgedeckt ist, keine unbeabsichtigten Änderungen enthalten sind und Dokumentation bzw. Beispiele bei geändertem Nutzerverhalten angepasst wurden. Fasse Änderung und Prüfergebnis knapp zusammen.

## Verbindliche Projektverträge

- Dateiklassifizierung folgt der Erweiterung; der Inhalt einer Datei darf eine abweichende Endung nicht eigenmächtig überstimmen. Das ist dokumentiertes Verhalten und durch Edge-Case-Tests abgesichert.
- Duplikaterkennung basiert auf SHA-256. Erkannte Duplikate werden übersprungen und nicht automatisch gelöscht. Bei `action: "copy"` bleibt die Quelldatei erhalten.
- Ablagepfade, Umbenennung bei Namenskonflikten, Dokumenten-/Medienziele und die Grenze von `storage.max_files_per_folder` (direkte Dateien, Unterordner zählen nicht) müssen konsistent mit Konfiguration und Tests bleiben.
- Konfigurationswerte müssen über die bestehende Konfigurationsschicht gelesen werden. Bei optionalen Integrationen oder fehlerhaften/fehlenden Metadaten ist das etablierte Fallback-Verhalten zu erhalten.
- Der Daemon verarbeitet Dateien wiederholt und parallel. Änderungen an gemeinsamem Zustand, Polling, Hash-Store oder Routing müssen auf Wiederholbarkeit und Thread-Sicherheit geprüft werden.
- Tests verwenden temporäre Verzeichnisse oder vorhandene Fixtures. Lokale, ignorierte Laufzeitdaten unter `tests/documents/` und `tests/media/` sind keine Fixtures und dürfen nicht als Testvoraussetzung dienen.

## Versionierung

- `filemind.__version__` in `filemind/__init__.py` ist die einzige Versionsquelle.
- Erhöhe die Version genau einmal pro abgeschlossener Implementierungsänderung, nicht pro bearbeiteter Datei oder Reparaturschritt.
- Folge SemVer: PATCH für Fehlerbehebungen, Diagnostik und Konfigurationskorrekturen; MINOR für rückwärtskompatible Funktionen; MAJOR für inkompatible Änderungen.
- Logge die aktive Version beim Daemonstart und im Performance-Event `run_start`.
- Prüfe vor der Änderung die aktuelle Version und setze den Zähler auf dieser Basis fort; setze ihn nicht zurück.

## Prüfungen

- Nutze für gezielte Änderungen den passenden Test unter `tests/`.
- Verlässlicher vollständiger Testlauf in dieser Umgebung:

  ```powershell
  .venv/Scripts/python.exe -m pytest tests/ -q
  ```

- Verwende den Workspace-Interpreter; `python` auf dem globalen PATH kann andere oder unvollständige Abhängigkeiten verwenden.
- Für Format- und Lint-Konventionen gilt `pyproject.toml` (Ruff, Zeilenlänge 100, Python-Zielversion 3.9). Prüfe nur geänderte Dateien, sofern kein breiterer Check nötig ist.
- Bei einem Fehlschlag unterscheide zwischen Regression der Änderung und bereits bestehendem bzw. umgebungsbedingtem Problem. Repariere keine unabhängigen Fehler im Rahmen der Anfrage.

## Fertig-Kriterien

Eine Anfrage ist erst abgeschlossen, wenn das verlangte Verhalten umgesetzt ist, passende Regressionstests vorhanden oder begründet entbehrlich sind, die relevanten Checks gelaufen sind und bekannte Einschränkungen klar benannt wurden. Bei Reviews stehen konkrete Fehler und Risiken mit Dateiverweisen vor einer allgemeinen Zusammenfassung.