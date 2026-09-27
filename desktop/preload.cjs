'use strict';
const { contextBridge, ipcRenderer } = require('electron');

async function invoke(channel, payload) {
  const reply = await ipcRenderer.invoke(channel, payload);
  if (!reply.ok) {
    const error = new Error(reply.error.message);
    error.code = reply.error.code;
    throw error;
  }
  return reply.value;
}

contextBridge.exposeInMainWorld('jev', Object.freeze({
  overview: options => invoke('jev:overview', options),
  openWork: id => invoke('jev:work', id),
  prepareModel: () => invoke('jev:prepare'),
  stopModel: () => invoke('jev:stop'),
  checkConnection: () => invoke('jev:connection'),
  selectProject: () => invoke('jev:project'),
  setup: (action, params = {}) => invoke('jev:setup', { action, params }),
}));
