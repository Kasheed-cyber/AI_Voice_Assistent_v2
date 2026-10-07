const http = require('http');
const path = require('path');
const express = require('express');
const WebSocket = require('ws');
const { setupSignaling } = require('./webrtc/signaling');
const { setupAudioHandler } = require('./audio/audioHandler');

const app = express();
const PORT = 8080;

app.use(express.static(path.join(__dirname, '..', '..', 'frontend')));

const server = http.createServer(app);

const signalingWss = new WebSocket.Server({ noServer: true });
const audioWss = new WebSocket.Server({ noServer: true });

server.on('upgrade', (request, socket, head) => {
    const { url } = request;

    if (url.startsWith('/ws/signaling')) {
        signalingWss.handleUpgrade(request, socket, head, (ws) => {
            signalingWss.emit('connection', ws, request);
        });
    } else if (url.startsWith('/ws/audio')) {
        audioWss.handleUpgrade(request, socket, head, (ws) => {
            audioWss.emit('connection', ws, request);
        });
    } else {
        socket.destroy();
    }
});

setupSignaling(signalingWss);
setupAudioHandler(audioWss);

server.listen(PORT, '0.0.0.0', () => {
    console.log('\n===========================================');
    console.log(`Сервер запущен: http://localhost:${PORT}`);
    console.log(`Сигнализация: ws://localhost:${PORT}/ws/signaling`);
    console.log(`Аудио-канал:  ws://localhost:${PORT}/ws/audio`);
    console.log('===========================================\n');
});