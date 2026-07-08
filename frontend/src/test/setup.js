import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

class MockEventSource {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.onopen = null;
    this.onerror = null;
    this.onmessage = null;
    this.closed = false;
    MockEventSource.instances.push(this);
  }

  emit(data) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }

  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  fail() {
    this.readyState = 2;
    this.onerror?.();
  }

  close() {
    this.closed = true;
  }
}

globalThis.MockEventSource = MockEventSource;
globalThis.EventSource = MockEventSource;

beforeEach(() => {
  MockEventSource.instances = [];
});

afterEach(() => {
  cleanup();
});
