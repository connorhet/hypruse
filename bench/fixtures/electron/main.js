// The benchmark's Electron app: a small settings page whose every change is
// written to the state file named on the command line, so a task is judged by
// what the app holds, not by what hypruse reports. Started with any Electron:
//   electron fixtures/electron STATE
const fs = require('fs');
const path = require('path');
const { app, BrowserWindow, ipcMain } = require('electron');

const state = process.argv.find((a) => a.endsWith('.json'));
ipcMain.on('state', (_event, value) => fs.writeFileSync(state, JSON.stringify(value)));

app.whenReady().then(() => {
  const win = new BrowserWindow({
    width: 520, height: 420, title: 'Bench app',
    webPreferences: { preload: path.join(__dirname, 'preload.js') },
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, 'index.html'));
});
app.on('window-all-closed', () => app.quit());
