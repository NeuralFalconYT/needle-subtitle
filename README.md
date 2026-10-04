# Needle Subtitle

A simple subtitle generator using **Cactus Needle** and **Silero VAD**.

It generates three subtitle formats from video or audio files:

- **YouTube / Horizontal** — sentence-level subtitles
- **Shorts / Reels** — short, punchy subtitle cues
- **Word Level** — one cue per word

## Model

This project uses **Cactus Needle / Needle 3**.

**Model:**  
https://huggingface.co/Cactus-Compute/needle3

**Original project / credit:**  
https://github.com/cactus-compute/needle

Please refer to the upstream Cactus project and model page for the applicable model, software, and usage terms.

## Requirements

- Windows
- Python 3.10+ recommended
- FFmpeg installed and available in system PATH
- Internet connection for the first model download

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/NeuralFalconYT/needle-subtitle.git
cd needle-subtitle
```

### 2. Install Python dependencies

```bash
python -m pip install -r requirements.txt
```

Current dependencies:

```text
cactus-needle==3.1.0
silero-vad==6.2.3
gradio==5.50.0
```

## Install FFmpeg

FFmpeg is required for audio/video processing.

Download FFmpeg:

https://ffmpeg.org/download.html

After installing, add the FFmpeg `bin` directory to your Windows **PATH**.

Verify the installation:

```bash
ffmpeg -version
```

If installed correctly, FFmpeg version information will be displayed.

## Run

Start the application:

```bash
python app.py
```

Then open the local Gradio URL shown in the terminal, usually:

```text
http://127.0.0.1:7860
```

## Usage

You can either enter the full path to a video/audio file or drag and drop a file into the Gradio interface.

Select the language and start subtitle generation.

## Output

When a file path is entered directly, subtitle files are saved next to the input file.

For drag-and-drop uploads, outputs are saved under:

```text
./needle_output/<filename>/
```

## Supported Languages

- English
- French
- Spanish
- German
- Dutch
- Italian
- Polish

## How It Works

```text
Video / Audio
      ↓
FFmpeg → 16 kHz mono WAV
      ↓
Silero VAD
      ↓
Smart ≤29 second chunks
      ↓
Cactus Needle / Whistle
      ↓
Word timestamps
      ↓
SRT generation
      ├── YouTube / Horizontal
      ├── Shorts / Reels
      └── Word Level
```

Long audio is automatically divided into speech-aware chunks before transcription.

## Credits

This project uses:

### Cactus Needle / Needle 3

https://huggingface.co/Cactus-Compute/needle3

### Cactus Needle Source Repository

https://github.com/cactus-compute/needle

### Silero VAD

https://github.com/snakers4/silero-vad

## Licensing

This repository is an application/wrapper around third-party software and models.

The third-party components used by this project may have their own separate licenses and terms. Please review the upstream sources before redistribution or other uses:

- Cactus Needle / Needle 3:  
  https://huggingface.co/Cactus-Compute/needle3

- Cactus Needle source:  
  https://github.com/cactus-compute/needle

- Silero VAD:  
  https://github.com/snakers4/silero-vad

## Disclaimer

This project does not claim ownership of the third-party models, runtimes, or libraries used by the application. All applicable rights and terms remain with their respective upstream authors and projects.
