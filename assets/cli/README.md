# Pearl terminal art

The portrait uses the original BopBop character artwork: its silhouette,
face, and pearly colors. `pearl.svg` embeds the source image with a clip
around the character. Eye and mouth poses reuse the original laughing and
winking emotes in `expressions/`, aligned over the face with feathered masks.
The body, highlights, and cheeks stay in place.

The greeting follows a quick blink, a held cheerful smile, and a wink,
then returns to the original expression. References:

- [Anime smile and wink animation](https://tenor.com/view/anime-cute-smile-wink-gif-12543366)
- [Live2D automatic eye blinking](https://docs.live2d.com/en/cubism-sdk-manual/autoeyeblink/)
- [Live2D expression parameters](https://docs.live2d.com/en/cubism-editor-manual/standard-parameter-list/)

The CLI reads the checked-in ANSI frames using only the Python standard
library. No image converters or font packages are needed at runtime.

To rebuild the portraits with [Chafa](https://hpjansson.org/chafa/):

```bash
python3 scripts/generate-cli-art.py
```

The assets were generated with Chafa 1.14.0 in true-color symbol mode using
half blocks and quadrants. Wide frames are 34×16 cells; compact frames are
22×10 cells. Generated files contain SGR colors and printable glyphs only.

The wordmark was rendered with [Bit](https://github.com/paulilaaso/bit) 0.4.0,
using its `glispbold` font:

```bash
bit -font glispbold -scale -1 -color '#a6d9e0' \
    -gradient '#b1a5dd' -direction right bopbop > assets/cli/wordmark.ansi
```
