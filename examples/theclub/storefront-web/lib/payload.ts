// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/** The local deployment's model can drift from the component schemas — a string
 * where an array belongs ("dimensions": "price, battery"), arrays carrying
 * non-strings. The generative components normalize before rendering so a drifted
 * payload renders what it can instead of throwing. */
export function stringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
  }
  if (typeof value === "string" && value.trim()) {
    return [value.trim()];
  }
  return [];
}

/** The array the model meant to send, or none — never a string or object the
 * caller would .map() off a cliff. */
export function listOf<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}
