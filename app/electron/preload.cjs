'use strict';
// Exposes a minimal, promise-based desktop API to the renderer. ipcRenderer itself never crosses the bridge;
// listeners receive only the payload, not the IPC event.
const { contextBridge, ipcRenderer } = require('electron');

const PREFIX = 'sage-desktop:';

async function invoke(channel, ...args) {
  const r = await ipcRenderer.invoke(PREFIX + channel, ...args);
  if (!r || r.ok !== true) throw new Error((r && r.error) || 'DESKTOP_ERROR');
  return r.data;
}

function subscribe(channel, callback) {
  if (typeof callback !== 'function') throw new TypeError('callback must be a function');
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(PREFIX + channel, listener);
  return () => ipcRenderer.removeListener(PREFIX + channel, listener);
}

contextBridge.exposeInMainWorld(
  'sageDesktop',
  Object.freeze({
    getInfo: () => invoke('info'),
    pickFolder: (workspaceId) => invoke('folders:pick', workspaceId),
    listFolders: (workspaceId) => invoke('folders:list', workspaceId),
    removeFolder: (folderId) => invoke('folders:remove', folderId),
    setPrimaryFolder: (folderId) => invoke('folders:primary', folderId),
    openFolder: (folderId) => invoke('folders:open', folderId),
    listDir: (folderId, relPath) => invoke('folders:readdir', folderId, relPath),
    startIndex: (target) => invoke('index:start', target),
    cancelIndex: (target) => invoke('index:cancel', target),
    onIndexProgress: (callback) => subscribe('index:progress', callback),
    checkConnection: () => invoke('connection:check'),
    checkForUpdates: () => invoke('update:check'),
    getUpdateState: () => invoke('update:state'),
    downloadUpdate: () => invoke('update:download'),
    installUpdate: () => invoke('update:install'),
    onUpdateState: (callback) => subscribe('update:state', callback),
    openExternal: (url) => invoke('open-external', url),
    setAppIcon: (style) => invoke('app-icon:set', style),
  }),
);
