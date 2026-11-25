# Vendored files

Copied by hand from npm packages; update by hand and change this file with them.

| File | Package | Version | Source | sha256 |
|---|---|---|---|---|
| `htmx.min.js` | `htmx.org` (`dist/htmx.min.js`) | 2.0.11 | npm | `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717` |
| `sse.min.js` | `htmx-ext-sse` (`dist/sse.min.js`) | 2.2.4 | npm | `98a46496de0c3605fbffdce9167ba427bdd9553184f83f149c261891a92c0136` |
| `pico.classless.min.css` | `@picocss/pico` (`css/pico.classless.min.css`) | 2.1.1 | npm | `61207a40ffc02a42d1e50143651c121beab70ed413c934c1ff84fa263ba436b0` |

Pico's inline `data:` SVG icons (checkbox tick, select chevron, `<details>` chevron, `aria-busy` spinner, date and search icons) are refused by the UI's `img-src 'self'` policy, so templates must not rely on them.

## Licence

`htmx.org` and `htmx-ext-sse` are under the Zero-Clause BSD licence (0BSD), copied from `htmx.org` 2.0.11:

```
Zero-Clause BSD
=============

Permission to use, copy, modify, and/or distribute this software for
any purpose with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED “AS IS” AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES
OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE
FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY
DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN
AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT
OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

`@picocss/pico` is under the MIT licence, copied from `@picocss/pico` 2.1.1:

```
MIT License

Copyright (c) 2019-2024 Pico

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
