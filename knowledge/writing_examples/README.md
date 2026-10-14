# Writing examples

Put **real posts the founder wrote** here, one Markdown file per post. They teach the AI the
founder's voice. Never add AI-generated posts as examples.

File format (`knowledge/writing_examples/my-post-name.md`):

```text
---
title: Why we stopped estimating in hours
category: engineering
approved: true
---
The post text exactly as it was published on LinkedIn.
```

- Only files with `approved: true` are used.
- An empty folder is fine; posts are then written from the style guide alone.
- After adding or changing files, run `uv run python manage.py sync_knowledge`.
