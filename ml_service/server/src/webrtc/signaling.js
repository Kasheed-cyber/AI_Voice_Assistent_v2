const WebSocket = require('ws');

// Хранилище подключённых клиентов
const clients = new Map();
let waitingConversationId = null;
let conversationSeq = 0;

function makeConversationId() {
    conversationSeq += 1;
    return `conversation_${Date.now()}_${conversationSeq}`;
}

function setupSignaling(wss) {
    wss.on('connection', (ws, req) => {
        const clientId = Date.now() + '_' + Math.random().toString(36).substr(2, 9);
        let conversationId = waitingConversationId;
        if (!conversationId) {
            conversationId = makeConversationId();
            waitingConversationId = conversationId;
        } else {
            waitingConversationId = null;
        }
        console.log(`[Signaling] Клиент подключён: ${clientId}, conversation=${conversationId}`);

        clients.set(clientId, { ws, id: clientId, conversationId });

        // Уведомляем клиента о его ID и общем ID разговора.
        ws.send(JSON.stringify({ type: 'init', id: clientId, conversation_id: conversationId }));

        ws.on('message', (message) => {
            try {
                const data = JSON.parse(message);

                // Пересылаем сигнальные сообщения только участникам того же разговора.
                if (['offer', 'answer', 'candidate'].includes(data.type)) {
                    for (const [otherId, client] of clients.entries()) {
                        if (otherId !== clientId && client.conversationId === conversationId && client.ws.readyState === WebSocket.OPEN) {
                            client.ws.send(JSON.stringify({
                                ...data,
                                from: clientId
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
            if (waitingConversationId === conversationId) waitingConversationId = null;

            // Уведомляем только участников этого разговора.
            for (const [_, client] of clients.entries()) {
                if (client.conversationId === conversationId && client.ws.readyState === WebSocket.OPEN) {
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