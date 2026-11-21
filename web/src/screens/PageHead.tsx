// Шапка сторінки квартири й оренди: фото на всю ширину і картка з адресою й фактами, що на нього наїжджає.

import type { ReactNode } from "react";

import type { Photo } from "../api/client";
import { PhotoSlider, type PhotoSource } from "./Photos";

export type Fact = { label: string; value: string };

export function PageHead({
  photos,
  source,
  chip,
  aside,
  badge,
  title,
  facts,
  empty,
}: {
  photos: Photo[];
  source: PhotoSource;
  chip: ReactNode;
  aside?: ReactNode;
  /** Маленький значок праворуч у рядку статусу (напр. щиток власності квартири). */
  badge?: ReactNode;
  title: string;
  facts: Fact[];
  /** Що показати замість фото, коли їх немає (напр. «Додати фото» власнику). */
  empty?: ReactNode;
}) {
  const hero = photos.length > 0;
  return (
    <>
      {hero ? (
        <div className="page-hero">
          <PhotoSlider photos={photos} source={source} compact />
        </div>
      ) : (
        empty
      )}
      <section className={`card page-head${hero ? " over-photo" : ""}`}>
        <div className="page-head-top">
          {chip}
          {aside && <span className="hint small">{aside}</span>}
          {badge}
        </div>
        <h1>{title}</h1>
        {facts.length > 0 && (
          <dl className="facts">
            {facts.map((f) => (
              <div key={f.label}>
                <dt>{f.label}</dt>
                <dd>{f.value}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>
    </>
  );
}
