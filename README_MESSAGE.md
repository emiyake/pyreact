# Componente Message

Componente unificado de output do framework. Cobre todos os casos de exibição de informações.

## Uso

```python
from message import Message

@component
def MyComponent():
    return [
        # Mensagem de chat (aparece como balão no browser)
        Message(
            key="welcome",
            text="Olá! Bem-vindo!",
            sender="assistant",
            message_type="info",
            trigger="mount",
        ),

        # Mensagem do usuário
        Message(
            key="user_msg",
            text="Esta é uma mensagem do usuário",
            sender="user",
            message_type="chat",
        ),

        # Aviso do sistema
        Message(
            key="warning",
            text="Atenção!",
            sender="system",
            message_type="warning",
        ),

        # Log (apenas stdout, não aparece como balão de chat)
        Message(
            key="debug",
            text="Informação de debug",
            sender="system",
            message_type="log",
            trigger="mount",
        ),
    ]
```

## Parâmetros

- `text`: Texto da mensagem
- `sender`: Quem enviou (`"user"`, `"assistant"`, `"system"`)
- `message_type`: Tipo da mensagem:
  - `"chat"` — mensagem de conversa (balão de chat)
  - `"info"` — informação
  - `"warning"` — aviso
  - `"error"` — erro
  - `"log"` — log de terminal (apenas stdout, não vai para o chat)
- `trigger`: Quando disparar (`"mount"` ou `"change"`)
- `key`: Chave única para o componente

## Comportamento por message_type

### Tipos estruturados (`chat`, `info`, `warning`, `error`)

| Modo     | Comportamento                                              |
|----------|------------------------------------------------------------|
| Terminal | `MessageBuffer.append()` + `print()` no stdout             |
| Web      | `MessageBuffer.append()` → WebSocket canal MSG (sem print) |

### Tipo `log`

| Modo     | Comportamento                          |
|----------|----------------------------------------|
| Terminal | `print()` no stdout                    |
| Web      | `print()` → stdout redirecionado → WebSocket canal STDOUT (logs) |

## Deduplicação

Cada mensagem estruturada carrega um `msg_id` único (counter monotônico). O frontend ignora mensagens com IDs já renderizados, evitando duplicação em:
- Replay de mensagens no reconnect (handler "hello")
- Re-renders de componentes que re-emitem a mesma mensagem

## Integração com o Sistema

Os componentes foram integrados nos seguintes locais:

1. **QAHome**: Mensagem de boas-vindas
2. **QAAgent**: Respostas do assistente + loading/error
3. **GuardRail**: Avisos de toxicidade
4. **Home**: Mensagem de boas-vindas + logs de rotas
5. **About**: Informações da página + ajuda
6. **NotFound**: Mensagem 404

## Configuração do Socket

O sistema suporta dois canais de mensagens no WebSocket:

1. **STDOUT** (`logs`): Para logs de terminal (message_type="log" e prints diretos)
2. **MSG** (`chat`): Para mensagens estruturadas (message_type="chat/info/warning/error")
