const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('bench', { save: (value) => ipcRenderer.send('state', value) });
