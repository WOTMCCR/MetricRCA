# Screenshots

No fake screenshot placeholders are committed.

To reproduce UI screenshots:

```bash
PATH=.venv/bin:$PATH make up
PATH=.venv/bin:$PATH make seed
PATH=.venv/bin:$PATH make api
npm run dev --prefix frontend
```

Open `http://127.0.0.1:5173`, submit a question such as
`Why did yesterday channel=paid_ads GMV drop?`, and capture the dashboard after
the API returns persisted report, evidence, SQL audit, trace, memory, and eval
panels.

This file is a non-P0 artifact note; it avoids representing unverified images as
real UI screenshots.
