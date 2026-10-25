# Neural Sprite

A desktop pixel-art editor built with Python and PySide6, with AI sprite generation and refinement through OpenAI or a local ComfyUI server.

## Launch

1. Extract the ZIP.
2. Open the `NeuralSprite` folder in PowerShell or Command Prompt.
3. Run:

   ```powershell
   py main.py
   ```

Install dependencies if needed:

```powershell
py -m pip install -r requirements.txt
```

## Screen color picker

Open the **Colors** dock, choose the **Color Picker** tab, then click **Pick Screen Color…**. The screen dims slightly; click any visible pixel to sample its color. Press `Esc` to cancel. The picked color becomes the active drawing color and is added to My Colors.

## Build a Windows installer

On Windows, extract the ZIP and double-click `build_installer.bat`. The script checks `python` and `py` first, then searches common per-user and machine-wide Python install folders (including Python 3.14) so Python does not have to be on PATH. It installs the app dependencies and PyInstaller, then builds a standalone application in `dist\Neural Sprite`. If Inno Setup 6 is installed, it also compiles `installer_output\NeuralSprite_Setup_v0.28.exe`. If Inno Setup is not installed, the standalone app is still built and can be launched from `dist\Neural Sprite\Neural Sprite.exe`; install Inno Setup 6 from https://jrsoftware.org/isdl.php and rerun the script to make the conventional setup EXE. The build must run on Windows; this source ZIP does not itself contain a prebuilt Windows executable.

## AI tools

- **AI → Generate New Sprite…** creates a sprite from a text prompt. The final dimensions are configurable and the result opens in a new tab.
- **AI → Refine Current Sprite…** sends a merged PNG of the current canvas as an image-to-image reference. The result opens in a separate tab; the original project is unchanged.
- **View → AI Provider Settings…** selects **OpenAI Cloud** or **Local ComfyUI** and configures the provider.
- Existing PixelForge AI settings are migrated to Neural Sprite settings when the AI tools are first used. Existing `.pfa` projects remain compatible.

## ComfyUI quality controls

When **Local ComfyUI** is selected, the generation dialog provides:

- **Fast:** 18 sampling steps and a 768-pixel working long edge.
- **Balanced:** 30 steps and a 1024-pixel working long edge.
- **High Detail:** 45 steps and a 1280-pixel working long edge.
- **Custom:** manually set steps, CFG, sampler, scheduler, and working resolution.
- **Optional second refinement pass:** runs image-to-image refinement on the first generated result, using the selected strength. This can improve component definition but may also change details; it is off by default.
- **Mechanical-structure guidance:** adds explicit guidance for connected, coherent components, including continuous tank-track loops and aligned road wheels.
- **Negative prompt:** specify unwanted artifacts, disconnected components, broken geometry, or other details to avoid.
- **Generation diagnostics:** reports provider, operation, elapsed time, final dimensions, selected preset, sampling parameters, working resolution, and second-pass status after completion.

These are sampling controls, not guarantees of correctness. More steps and larger resolutions can improve detail at the cost of generation time and VRAM. Checkpoint training and model family strongly affect results. The built-in workflow targets standard Stable Diffusion / SDXL checkpoints available through ComfyUI's `CheckpointLoaderSimple`; Flux and specialized workflows may require a different graph.

## Resolution and pixel conversion

SDXL generally needs a working resolution near 1024 pixels on the long edge. Generating directly on a tiny latent grid (such as 32 × 32) can produce incoherent shapes. Neural Sprite therefore generates at an aspect-ratio-matched working resolution, then converts the result to the requested final sprite dimensions using nearest-neighbor sampling. This preserves hard edges but does not guarantee that every detail survives reduction. Prompts should emphasize a clear silhouette, connected geometry, and simple readable components.

## Compatibility

- Existing `.pfa` projects continue to use their original internal file-format identifier.
- On first AI use, saved provider settings from the previous PixelForge AI application are copied into Neural Sprite's settings if the new settings profile is empty.
- OpenAI Cloud requires an API key and may incur API charges. Local ComfyUI does not require an OpenAI key.


Build script note (v0.28.4): the Windows builder now checks standard per-user Python installation paths before commands on PATH. This avoids false detection from Windows App Execution Aliases that can cause `where python` to succeed while `python` itself fails.


## v0.29 selection fixes
- Live rectangle/ellipse selection preview is drawn directly as a high-contrast, translucent overlay while dragging.
- Selection dragging continues when the cursor leaves the canvas; preview and final selection are clipped to canvas bounds.
- Installer version is updated to 0.29.
