# Dicky

Offline dictionary for [Ulauncher](https://ulauncher.io/). Type `d word` and get instant definitions, no internet needed.

Uses [sdcv](https://github.com/huzheng001/stardict-3) (StarDict console dictionary) for lookups and [fzf](https://github.com/junegunn/fzf) for fuzzy search.

![Screenshot](screenshot.png)

## Features

- Instant offline lookups from any StarDict dictionary
- Fuzzy search and autocomplete as you type (powered by fzf)
- Typo correction via edit-distance matching
- Case-insensitive matching
- Numbered definitions shown as separate items
- Etymology/origin displayed where available
- Copy definition to clipboard on Enter
- Link to Wiktionary for the full entry
- Works with multiple dictionaries simultaneously

## Requirements

- [Ulauncher](https://ulauncher.io/) v5 or v6
- [sdcv](https://github.com/huzheng001/stardict-3) (StarDict console dictionary)
- [fzf](https://github.com/junegunn/fzf) (fuzzy finder)
- One or more StarDict dictionaries

## Install

### 1. Install sdcv and fzf

**Fedora/RHEL:**

```bash
sudo dnf install sdcv fzf
```

**Ubuntu/Debian:**

```bash
sudo apt install sdcv fzf
```

**Arch:**

```bash
sudo pacman -S sdcv fzf
```

### 2. Install a dictionary

Dicky works with any dictionary in [StarDict format](https://github.com/huzheng001/stardict-3/blob/master/dict/doc/StarDictFileFormat). You need at least one.

**GCIDE (GNU Collaborative International Dictionary of English)** is free and comprehensive:

- Fedora: `sudo dnf install stardict-dic-en` (if available), or download the StarDict files
- The GCIDE StarDict package is available from [dict.org mirrors](https://ftp.dict.org/pub/dict/)

**Wiktionary exports** are another good free option. Community-maintained StarDict builds of Wiktionary data are available from:

- [dictinfo.com](https://www.dictinfo.com/) (regularly updated English Wiktionary in StarDict format)
- [Vuizur/Wiktionary-Dictionaries](https://github.com/Vuizur/Wiktionary-Dictionaries) on GitHub

Place dictionary files in `~/.stardict/dic/`. Each dictionary lives in its own subfolder containing `.ifo`, `.idx` and `.dict` (or `.dict.dz`) files.

```
~/.stardict/dic/
  my-dictionary/
    My_Dictionary.ifo
    My_Dictionary.idx
    My_Dictionary.dict.dz
```

### 3. Verify sdcv works

```bash
sdcv "hello"
```

If this shows a definition, you're good.

### 4. Install the extension

Open Ulauncher preferences, go to Extensions, click "Add extension" and paste:

```
https://github.com/no-faff/ulauncher-dicky
```

## Usage

Open Ulauncher and type:

- `d colour` - look up a word
- `d flocc` - autocomplete/browse words starting with "flocc"
- `d colur` - typo? suggestions appear automatically

Press Enter on a definition to copy it to clipboard. Select "Open on Wiktionary" to view the full entry online.

## Keyword

The default keyword is `d`. Change it in Ulauncher preferences under Extensions.

## Licence

MIT
