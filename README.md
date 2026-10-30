# book2podcast

Turn the books in your own library into a podcast series, one NotebookLM-style
"deep dive" episode per chapter, entirely on local hardware.

```
book2podcast "Course in General Linguistics"
```

finds the book in Calibre-Web, splits it into chapters (EPUB or PDF, including
old scans), writes and voices a two-host conversation about each chapter, and
saves the episodes as tagged MP3s in `~/Podcasts/<Book title>/`. Nothing leaves
your machine: the LLM, the voices and the PDF reader all run locally, built to
share a single 6 GB laptop GPU by taking turns.

## What it does

```
Calibre-Web ──OPDS──▶ EPUB / PDF
                         │
          PDF only:  Docling (layout model + OCR, GPU) ──▶ text blocks with headings
                         │
                 chapter detection (rules + local LLM judge) ──▶ chapters.json
                         │
            per chapter:  Open Notebook ──▶ outline + script (Ollama, GPU)
                                       └──▶ voices (Speaches / Kokoro, GPU)
                         │
                 ~/Podcasts/<Book>/NN - Chapter.mp3   (ID3: album, track, title)
                         │
             optional:  Home Assistant media library  /media/Podcasts/<Book>/
```

A typical chapter becomes a 25-30 minute episode in about 15 minutes. A
12-chapter academic anthology took a little over 3 hours, unattended.

## The stack

| Component | Role | Runs on |
|---|---|---|
| [Calibre-Web](https://github.com/janeczku/calibre-web) (any fork with OPDS) | the book library; books are searched and downloaded over its OPDS catalog | anywhere on your network |
| [Ollama](https://ollama.com) + Gemma 4 E4B (62k context, text-only build) | judges chapter breaks, summarises chapters, writes the podcast scripts | GPU |
| [Open Notebook](https://github.com/lfnovo/open-notebook) | podcast pipeline: outline, two-host transcript, audio assembly | Docker |
| [Speaches](https://github.com/speaches-ai/speaches) + [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | text-to-speech for the two hosts | Docker, GPU |
| [Docling](https://github.com/docling-project/docling) + RapidOCR | reads PDFs: layout, headings, running headers, OCR for image-only scans | own venv, GPU |
| ffmpeg | tags the finished MP3s | host |

Tested on an RTX 4050 Laptop GPU (6 GB VRAM), i7-13700H, 15 GB RAM, Ubuntu.

### Taking turns on one GPU

6 GB cannot hold the LLM, Kokoro and Docling at once, so `book2podcast`
schedules them:

1. **Docling first, alone.** Before reading a PDF, every Ollama model is
   unloaded and Kokoro released. Docling runs in its own process and exits,
   which frees all its memory, before the LLM is loaded for chapter detection.
   It reads in 40-page chunks so RAM stays flat (an 800-page scan peaks around
   6 GB instead of growing until the system kills it).
2. **Per chapter: LLM, then voices.** Open Notebook writes the script with the
   LLM, then Speaches voices it with Kokoro.
3. **Between chapters**, the next LLM must not land on a GPU still holding
   Kokoro (it would be pushed partly onto the CPU and crawl). Speaches unloads
   Kokoro after 30 s idle (`WHISPER__TTL=30`); `book2podcast` waits for that.
   Speaches' ONNX runtime also *leaks* GPU memory across long episodes
   (we saw 4-6 GB held with no model loaded), which only a restart returns, so
   when memory stays held past the idle timer, `book2podcast` restarts the
   container (see [the sudo rule](#let-book2podcast-restart-speaches)).

## Chapter detection

This is the hard part, and most of the code. "Chapter" means very different
things in an EPUB novel, a scanned 1903 translation, an anthology of essays
that each number their own sections, a textbook with exercise sections, or a
bilingual Loeb edition with Greek on the facing pages.

**1. Possible breaks** are collected from every structural signal available:

- EPUB: the table of contents (with its nesting: Part > Chapter > section),
  file boundaries, `h1`/`h2` headings.
- PDF: bookmarks with their levels; Docling's detected headings; and, for
  books without bookmarks, the **printed contents page rebuilt into bookmarks**:
  `"Facing Plato's Problem ........ 51"` is parsed, and the offset between
  printed and PDF page numbers is found from the entries whose titles appear in
  the text. This recovers chapters whose heading the OCR lost entirely.
- Lines like `Chapter 7` / `PART TWO`.

**2. Rules settle what has an unambiguous shape**, before any LLM is asked:
contents pages (their lines are other chapters' titles, or bare page numbers);
running headers (short lines repeated in the page margin); decimal sections
(`3.1`), bracketed examples (`(5) I had a book stolen.`), `§ 54`, glossary
letters; numbered sections in books whose numbering restarts under each essay;
bookmarks nested under a chapter (unless they are chapter-sized themselves);
headings that a short printed chapter list does not mention.

**3. A local LLM judges the rest**, one break at a time. It sees the text just
before and after the break, an outline of the nearby breaks (indented by level),
and the book's own contents page, and classifies the break as chapter, part,
section, front matter or back matter, with a clean title in the book's language.

**4. Safety nets** keep a misjudgment from losing text: front matter after the
first chapter, back matter before the last, or a "part" divider followed by a
whole chapter of text are all kept as chapter content. Short chapters ride along
with the next one instead of vanishing. Duplicate entries merge. Commentary
headings ("Reflections", "Exercises") join the chapter before them. Blocks in a
different script from the rest of the book (the Greek of a Loeb edition) are
dropped so the podcast writer never sees them.

**5. A global check** catches implausibly fine splits (more than 40 "chapters"
with a median under 1,500 words): numbered chapter headings are kept, or the
LLM picks episode-sized units from the full list of titles and lengths.

The result is cached in `~/.cache/book2podcast/<book>/chapters.json`; edit it
to adjust a split by hand.

### How well it works

Developed against 35 books: public-domain test cases plus a personal library of
academic PDFs, old scans without text layers, anthologies, textbooks, Turkish
titles and EPUB novels. After the last round, **28 split correctly or
plausibly, 5 came out too fine** (sections promoted to chapters, mostly in scans
whose chapters contain titled sections with nothing else to tell them apart,
up to ~1.5x the right number of episodes), and **none lose text**.

| Public-domain test book | Structure | Result |
|---|---|---|
| *The Adventures of Sherlock Holmes* (EPUB) | 12 stories, each with sections I, II, III | 12 ✅ |
| *A Tale of Two Cities* (EPUB) | 3 Books, 45 chapters, contents page as text | 45 ✅ |
| *Frankenstein* (EPUB) | Letters 1-4, then 24 chapters | 27 ✅ (letter 3 is a paragraph and rides with 4) |
| *Walden* (EPUB) | 18 unnumbered chapters + an essay | 19 ✅ |
| *The Prince*, 1903 scan (PDF) | OCR'd, no bookmarks, running headers | 26 ✅ |
| *The Time Machine*, 1922 scan (PDF) | OCR'd, Roman numerals | 14 ✅ |
| Conrad, *Youth / Heart of Darkness / The End of the Tether* (PDF scan) | chapter numerals missing from the OCR | 12 of ~18 (missing numerals merge their chapters) |

## Installation

### 1. Ollama and the model

Install [Ollama](https://ollama.com) and make it reachable from Docker (it must
listen beyond localhost for Open Notebook to reach it):

```
sudo systemctl edit ollama      # add:  [Service]  Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl restart ollama
```

Create the model from a Gemma 4 E4B GGUF with [`ollama/Modelfile`](ollama/Modelfile)
(62k context; the text-only build keeps it on a 6 GB GPU):

```
cd ollama && ollama create gemma4-abliterated-e4b-text -f Modelfile
```

Any other Ollama model works too: set `split_model` in the config and pass
`LLM_MODEL=...` to `setup-profiles.sh`.

### 2. Open Notebook and Speaches

```
mkdir -p ~/open-notebook && cp docker-compose.example.yml ~/open-notebook/docker-compose.yml
cd ~/open-notebook        # change OPEN_NOTEBOOK_ENCRYPTION_KEY in docker-compose.yml first
docker compose up -d
docker compose exec speaches uv tool run speaches-cli model download speaches-ai/Kokoro-82M-v1.0-ONNX
```

In the Open Notebook UI (http://localhost:8502), under **Manage → Models**:

- add an **Ollama** credential with base URL `http://host.docker.internal:11434`,
  and register `gemma4-abliterated-e4b-text:latest` as a language model;
- add an **OpenAI-compatible** credential with base URL `http://host.docker.internal:8969/v1`
  (Linux without the compose `extra_hosts` line: `http://172.17.0.1:8969/v1`),
  and register `speaches-ai/Kokoro-82M-v1.0-ONNX` as a text-to-speech model.

Then create the podcast profiles (two hosts, Alex and Sam, a long-form deep-dive
briefing, 10 segments):

```
./setup-profiles.sh
```

### 3. book2podcast

```
sudo apt install ffmpeg     # for MP3 tags (optional)
./install.sh                # or ./install.sh --no-pdf for EPUB only
```

`install.sh` puts the command in `~/.local/bin`, the Docling helper in
`~/.local/share/book2podcast`, and creates a Python 3.11 venv with Docling and
PyTorch (CUDA) for PDF support, about 6 GB.

### Let book2podcast restart Speaches

To work around the Speaches memory leak automatically, allow exactly one
command without a password (nothing else; unlike joining the `docker` group):

```
echo "$USER ALL=(root) NOPASSWD: /usr/bin/docker restart speaches" | sudo tee /etc/sudoers.d/book2podcast
sudo chmod 440 /etc/sudoers.d/book2podcast
```

Without it, `book2podcast` waits for Speaches instead, and if it stays stuck,
stops with instructions rather than failing every remaining chapter.

### Home Assistant media library (optional)

Finished episodes can also go straight into Home Assistant's media library,
one folder per book, so you can play them on any media player Home Assistant
controls:

```
Media → My media → Podcasts → The Philosophy of Linguistics → 01 - Introduction.mp3
```

```
book2podcast --setup-ha
```

asks for your Home Assistant URL and a long-lived access token (Profile →
Security → Long-lived access tokens; it must belong to an admin, since uploading
media needs admin rights), checks them, and saves them to the config file. If
another tool already keeps a token in a file (an MCP server setup, say), point
`ha_token_file` at that file instead of copying the secret.

From then on every finished episode is uploaded through Home Assistant's own
media upload API. That API accepts at most 20 MB per file, so the Home Assistant
copy is re-encoded to mono 64 kbps (48 or 32 for very long episodes), about
13 MB for half an hour of speech, with the same tags; the copy in `~/Podcasts`
keeps full quality. Re-running a book uploads only what is missing there, so it
also backfills episodes made before Home Assistant was set up. `--no-ha` skips
it for a run.

## Configuration

`~/.config/book2podcast/config.json` (environment variables override it):

| Key | Environment | Default |
|---|---|---|
| `calibre_url` | `CALIBRE_URL` | `http://localhost:8083` |
| `open_notebook_api` | `OPEN_NOTEBOOK_API` | `http://localhost:5055/api` |
| `open_notebook_episodes` | `OPEN_NOTEBOOK_EPISODES` | `~/open-notebook/notebook_data/podcasts/episodes` |
| `ollama_url` | `OLLAMA_URL` | `http://localhost:11434` |
| `speaches_url` | `SPEACHES_URL` | `http://localhost:8969` |
| `speaches_container` | `SPEACHES_CONTAINER` | `speaches` |
| `split_model` | `BOOK2PODCAST_SPLIT_MODEL` | `gemma4-abliterated-e4b-text:latest` |
| `profile` | `BOOK2PODCAST_PROFILE` | `deep-dive` |
| `ha_url` | `HA_URL` | none (Home Assistant upload off) |
| `ha_token` or `ha_token_file` | `HA_TOKEN` / `HA_TOKEN_FILE` | none |
| `ha_folder` | `HA_FOLDER` | `Podcasts` |

The first run asks for your Calibre-Web login (hidden input) and offers to save
it to `~/.config/book2podcast/calibre.json`, readable only by you. Run that
first command in a real terminal.

## Usage

```
book2podcast "Some Book"                   # every chapter, saved to ~/Podcasts/Some Book/
book2podcast "Some Book" --list            # only split, and show the chapters
book2podcast "Some Book" --chapters 3-7    # some chapters
book2podcast --file ./book.pdf             # a local EPUB or PDF instead of Calibre-Web
book2podcast "Some Book" -v                # also print every decision as it is made
```

Other options: `--resplit` (redo chapter detection), `--out DIR`,
`--no-download`, `--no-ha`, `--setup-ha`, `--profile NAME`, `--no-notebook`,
`--no-podcast`.

**Resuming.** Re-running a command picks up where it stopped: finished episodes
are skipped (and downloaded if missing from the folder), an episode that was
still being made when the run stopped is re-attached to, failed ones are redone.

**Progress.** A live status line shows each stage with an estimate of the time
left: Docling pages, breaks judged, summaries, and for each episode "writing
outline and script", then "recording voices: 37 lines of ~67 (55%)", read from
the files Open Notebook writes as it works. With `-v` every verdict is printed
with the rule or the model's reason behind it.

## Troubleshooting

- **`CUDA error: out of memory` / a chapter fails during voices.** Speaches
  leaked GPU memory. `docker restart speaches`, then re-run; set up the
  [sudo rule](#let-book2podcast-restart-speaches) so it happens automatically.
- **"Docling was killed ... out of memory".** Close large programs and re-run;
  Docling's work is cached per book once it finishes.
- **A book splits badly.** Edit `~/.cache/book2podcast/<book>/chapters.json`
  (merge entries by joining their `text`), or `--resplit -v` to see why.
- **"Several books match".** Use the exact title; in a terminal you can also
  pick from the list.

## Limitations

- Chapters whose number OCR dropped and whose title appears nowhere else merge
  with a neighbour (their text is kept).
- Long chapters get the same episode length as short ones (10 segments), so a
  20,000-word chapter is covered more briefly than a 5,000-word one.
- The local model is not fully deterministic; a re-split can differ slightly.

## License

GPL-3.0. See [LICENSE](LICENSE).
