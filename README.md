# Neural Sprite

A desktop pixel-art editor built with Python and PySide6, featuring AI sprite generation and refinement through OpenAI or a local ComfyUI server.

## Launch

1. Extract the ZIP archive.
2. Open the `NeuralSprite` folder in PowerShell or Command Prompt.
3. Run the application:

   ```powershell
   py main.py
   ```

### Install Dependencies

If the required packages are not installed, run:

```powershell
py -m pip install -r requirements.txt
```

## Screen Color Picker

1. Open the **Colors** dock.
2. Select the **Color Picker** tab.
3. Click **Pick Screen Color…**.
4. Click any visible pixel on your screen to sample its color.
5. Press `Esc` to cancel the operation.

The selected color becomes the active drawing color and is added to **My Colors**.

## Build a Windows Installer

On Windows, extract the ZIP archive and double-click `build_installer.bat`.

The build script:

- Searches for Python using the `python` and `py` commands, followed by common per-user and machine-wide installation locations, including Python 3.14.
- Installs the required dependencies and PyInstaller.
- Builds a standalone application in `dist\Neural Sprite`.
- Compiles a Windows installer if Inno Setup 6 is installed.

The expected installer output is:

`installer_output\NeuralSprite_Setup_v0.30.exe`

### If Inno Setup Is Not Installed

The build script can still create the standalone application. Launch it from:

`dist\Neural Sprite\Neural Sprite.exe`

To build the conventional Windows installer, install Inno Setup 6 from the official website and rerun the build script:

https://jrsoftware.org/isdl.php

**Note:** The build process must run on Windows. The source ZIP does not include a prebuilt Windows executable. You can find this in the latest release.

## AI Tools

### Generate a New Sprite

Select **AI → Generate New Sprite…** to generate a sprite from a text prompt. Final sprite dimensions can be configured, and the generated result opens in a new tab.

### Refine the Current Sprite

Select **AI → Refine Current Sprite…** to send a merged PNG of the current canvas as an image-to-image reference. The refined result opens in a separate tab, leaving the original project unchanged.

### AI Provider Settings

Select **View → AI Provider Settings…** to choose between:

- **OpenAI Cloud:** Uses OpenAI's cloud-based image generation capabilities and requires an API key.
- **Local ComfyUI:** Connects to a locally hosted ComfyUI server.

Existing PixelForge AI settings are migrated to Neural Sprite settings when the AI tools are first used. Existing `.pfa` project files remain compatible.

## ComfyUI Quality Controls

When **Local ComfyUI** is selected, the generation dialog provides the following quality presets.

### Fast

- 18 sampling steps.
- 768-pixel working resolution along the longer image edge.

Designed to reduce generation time.

### Balanced

- 30 sampling steps.
- 1024-pixel working resolution along the longer image edge.

Provides a balance between generation speed and detail.

### High Detail

- 45 sampling steps.
- 1280-pixel
