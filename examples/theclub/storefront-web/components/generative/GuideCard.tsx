// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { GuidePayload } from "@/lib/types";
import { listOf, stringList } from "@/lib/payload";
import ProductTile from "../ProductTile";

export default function GuideCard({ payload }: { payload: GuidePayload }) {
  const sections = listOf<NonNullable<GuidePayload["sections"]>[number]>(payload.sections).filter(
    (section) => section && typeof section.body === "string"
  );
  const relatedProducts = listOf<NonNullable<GuidePayload["related_products"]>[number]>(
    payload.related_products
  ).filter((product) => product && product.product_id);
  const sources = stringList(payload.sources);
  return (
    <section className="rounded-2xl border border-(--line) bg-(--card) p-4 shadow-(--shadow-sm)">
      <h3 className="text-[15px] font-semibold text-(--ink)">{payload.title}</h3>
      <div className="mt-2 space-y-3">
        {sections.map((section, index) => (
          <div key={index}>
            <div className="text-[13px] font-bold uppercase tracking-wide text-(--ink)">{section.heading}</div>
            <p className="mt-0.5 text-[15px] leading-relaxed text-(--ink)">{section.body}</p>
          </div>
        ))}
      </div>
      {relatedProducts.length ? (
        <div className="panel-scroll mt-3 flex gap-3 overflow-x-auto border-t border-(--line) pt-3">
          {relatedProducts.map((product) => (
            <ProductTile key={product.product_id} product={product} compact />
          ))}
        </div>
      ) : null}
      {sources.length ? (
        <p className="mt-3 break-all text-[11px] text-(--ink-soft)/80">Sources: {sources.join(" · ")}</p>
      ) : null}
    </section>
  );
}
