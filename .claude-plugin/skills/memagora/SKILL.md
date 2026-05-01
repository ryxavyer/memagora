---
name: memagora
description: MemAgora — mine projects and conversations into a searchable memory palace. Automated institutional knowledge extraction for teams. Use when asked about memagora, memory palace, mining memories, searching memories, agora data, or palace setup.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# MemAgora

A searchable memory palace for AI and curated institutional knowledge — mine projects and conversations, then search them semantically.

## Prerequisites

Ensure `mempalace` is installed:

```bash
mempalace --version
```

If not installed:

```bash
pip install mempalace
```

## Usage

MemPalace provides dynamic instructions via the CLI. To get instructions for any operation:

```bash
mempalace instructions <command>
```

Where `<command>` is one of: `help`, `init`, `mine`, `search`, `status`.

Run the appropriate instructions command, then follow the returned instructions step by step.
