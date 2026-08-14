import {
  AssistantRuntimeProvider,
  AuiIf,
  type ChatModelAdapter,
  ComposerPrimitive,
  MessagePartPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useLocalRuntime,
} from '@assistant-ui/react'
import { ArrowUpIcon } from 'lucide-react'
import type { ReactNode } from 'react'

const vantavAdapter: ChatModelAdapter = {
  async run({ messages, abortSignal }) {
    const lastUser = messages
      .slice()
      .reverse()
      .find((m) => m.role === 'user')
    const q = lastUser?.content
      .map((part) => (part.type === 'text' ? part.text : ''))
      .join('')
      .trim()

    const response = await fetch('/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ q }),
      credentials: 'same-origin',
      signal: abortSignal,
    })

    if (!response.ok) {
      throw new Error(`API error: ${response.statusText}`)
    }

    const data = await response.json()
    const text = typeof data.answer === 'string' ? data.answer : JSON.stringify(data)
    return { content: [{ type: 'text' as const, text }] }
  },
}

function RuntimeProvider({ children }: { children: ReactNode }) {
  const runtime = useLocalRuntime(vantavAdapter)
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root>
      <div className="message message-assistant">
        <MessagePrimitive.Parts>
          {({ part }) => (part.type === 'text' ? <MessagePartPrimitive.Text /> : null)}
        </MessagePrimitive.Parts>
      </div>
    </MessagePrimitive.Root>
  )
}

function UserMessage() {
  return (
    <MessagePrimitive.Root>
      <div className="message message-user">
        <MessagePrimitive.Parts>
          {({ part }) => (part.type === 'text' ? <MessagePartPrimitive.Text /> : null)}
        </MessagePrimitive.Parts>
      </div>
    </MessagePrimitive.Root>
  )
}

function Thread() {
  return (
    <ThreadPrimitive.Root className="chat-container">
      <ThreadPrimitive.Viewport className="thread-viewport">
        <AuiIf condition={(s) => s.thread.isEmpty}>
          <div className="empty-state">
            <h2>AI Assistant</h2>
            <p>Ask anything about your store, products, or orders.</p>
          </div>
        </AuiIf>
        <div className="messages-area">
          <ThreadPrimitive.Messages>
            {({ message }) =>
              message.role === 'user' ? <UserMessage /> : <AssistantMessage />
            }
          </ThreadPrimitive.Messages>
        </div>
        <ThreadPrimitive.ViewportFooter className="composer-footer">
          <ComposerPrimitive.Root className="composer">
            <ComposerPrimitive.Input
              asChild
              rows={1}
              placeholder="Ask the assistant..."
              style={{ width: '100%', background: 'transparent', border: 'none', color: 'inherit', font: 'inherit', resize: 'none', outline: 'none' }}
            >
              <textarea />
            </ComposerPrimitive.Input>
            <ComposerPrimitive.Send asChild>
              <button className="send-button" type="button">
                <ArrowUpIcon />
              </button>
            </ComposerPrimitive.Send>
          </ComposerPrimitive.Root>
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  )
}

export default function App() {
  return (
    <RuntimeProvider>
      <Thread />
    </RuntimeProvider>
  )
}
