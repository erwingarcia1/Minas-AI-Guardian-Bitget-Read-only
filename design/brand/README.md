# Brand assets

The Minas logo, supplied by the owner on 2026-10-22. It replaces the stroked
infinity mark from the 2026-08-19 design pass everywhere the product shows a
logo: the member portal, and the iOS and Android apps (decision 21g). The
ops dashboard stays unbranded on purpose (decision 2 on #82), so an operator
screen can never pass for a customer page.

**One master, every size derived from it.** Never edit a derived file by
hand. Run `scripts/brand-assets.py` and commit what it writes. The script
needs Pillow, which is not a dependency of the service.

```bash
python3 scripts/brand-assets.py clean <new-logo.png>   # only when a new logo is supplied
python3 scripts/brand-assets.py derive
```

`clean` exists because the logo as supplied carries a nearly transparent
square behind the mark (alpha about 5). It shows as a smudge on dark
surfaces and a grey box on white, and `clean` removes it.

## Files

| File | Size | Use |
|---|---|---|
| `minas-logo.png` | 2048, transparent | The master. Also used by the mockups in `design/portal/`. |
| `app-icon.png` | 1024, opaque | Expo `icon`. iOS wants the square filled, with no transparency. |
| `android-foreground.png` | 1024, transparent | Expo `android.adaptiveIcon.foregroundImage`. The mark sits inside the inner 66 of 108 dp, which no device mask clips. |
| `android-monochrome.png` | 1024, white silhouette | Expo `android.adaptiveIcon.monochromeImage`, for Android themed icons. |
| `splash.png` | 1024, transparent | The `image` of the `expo-splash-screen` plugin. |
| `../../sentinel/static/minas-mark.png` | 128, transparent | The portal's lockup. |
| `../../sentinel/static/favicon.png` | 64, transparent | Browser tab, served at `/brand/icon.png` and `/favicon.ico`. |
| `../../sentinel/static/apple-touch-icon.png` | 180, opaque | iPhone home screen, for anyone who saves the portal there. |

## The background colour

`#0d0f13` is the portal's `--bg`. It sits behind the mark on every opaque
icon. Use the same value for `android.adaptiveIcon.backgroundColor` and the
splash screen's `backgroundColor`, so the app opens on the same surface the
portal shows.

## Colours: the logo and the palette

The logo shades from a deep violet to a muted gold. The portal's identity
tokens are brighter: `--violet` `#8b5cf6` and `--gold` `#f0b429` in
`sentinel/brand.py`. They were not changed with the logo. Retuning the
palette to match is a separate decision.
