"use client";

import { useEffect, useRef, useState } from "react";

export interface StreamEvent {
  channel: string;
  data: Record<string, unknown>;
}

/** Hook simples pra consumir /api/stream (SSE) e acumular os últimos eventos por canal. */
export function useEventStream(channels: string[]) {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const source = new EventSource("/api/stream");
    sourceRef.current = source;

    for (const channel of channels) {
      source.addEventListener(channel, (e: MessageEvent) => {
        setEvents((prev) => [{ channel, data: JSON.parse(e.data) }, ...prev].slice(0, 100));
      });
    }

    return () => source.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channels.join(",")]);

  return events;
}
