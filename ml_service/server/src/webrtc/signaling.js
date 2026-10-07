const WebSocket = require('ws');

// Хранилище подключённых клиентов
const clients = new Map();

function setupSignaling(wss) {
    wss.on('connection', (ws, req) => {
        const clientId = Date.now() + '_' + Math.random().toString(36).substr(2, 9);
        console.log(`[Signaling] Клиент подключён: ${clientId}`);

        clients.set(clientId, { ws, id: clientId });

        // Уведомляем клиента о его ID
        ws.send(JSON.stringify({ type: 'init', id: clientId }));

        ws.on('message', (message) => {
            try {
                const data = JSON.parse(message);

                // Пересылаем сигнальные сообщения другим клиентам
                if (['offer', 'answer', 'candidate'].includes(data.type)) {
                    for (const [otherId, client] of clients.entries()) {
                        if (otherId !== clientId && client.ws.readyState === WebSocket.OPEN) {
                            client.ws.send(JSON.stringify({
                                ...data,
                                from: clientId
                            }));
                        }
                    }
                } else if (data.type === 'ai_notice') {
                    // Уведомление о подключенной услуге ИИ до начала обработки разговора.
                    console.log(`[Compliance] Уведомление о записи и ИИ-обработке отправлено участникам звонка ${data.callId || 'unknown'}`);
                    for (const [otherId, client] of clients.entries()) {
                        if (otherId !== clientId && client.ws.readyState === WebSocket.OPEN) {
                            client.ws.send(JSON.stringify({
                                type: 'ai_notice',
                                callId: data.callId || null,
                                text: data.text || 'Внимание! Разговор записывается и обрабатывается искусственным интеллектом для автоматической фиксации договорённостей.'
                            }));
                        }
                    }
                }
            } catch (err) {
                console.error('[Signaling] Ошибка парсинга:', err.message);
            }
        });

        ws.on('close', () => {
            console.log(`[Signaling] Клиент отключён: ${clientId}`);
            clients.delete(clientId);

            // Уведомляем остальных
            for (const [_, client] of clients.entries()) {
                if (client.ws.readyState === WebSocket.OPEN) {
                    client.ws.send(JSON.stringify({
                        type: 'peer-left',
                        id: clientId
                    }));
                }
            }
        });

        ws.on('error', (err) => {
            console.error(`[Signaling] Ошибка у ${clientId}:`, err.message);
        });
    });

    console.log('[Signaling] WebSocket-сервер сигнализации готов');
}

module.exports = { setupSignaling };