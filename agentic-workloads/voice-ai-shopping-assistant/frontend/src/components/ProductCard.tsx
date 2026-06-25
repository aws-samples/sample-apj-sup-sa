import type { LiveProduct } from "../types/contracts.live";

function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

/** Shared product/variant card: image, brand, price, quality, allergens/diet. */
export function ProductCard({ p }: { p: LiveProduct }) {
  const out = p.in_stock === false;
  return (
    <div className={`product-card ${out ? "out" : ""}`}>
      {p.image_url && (
        <div className="pc-img">
          <img src={p.image_url} alt="" loading="lazy" />
        </div>
      )}
      <div className="pc-head">
        <span className="pc-name">{p.name}</span>
        {p.quality_tier && (
          <span className={`pc-tier ${p.quality_tier}`}>{p.quality_tier}</span>
        )}
      </div>
      <div className="pc-sub">
        {p.brand && <span className="pc-brand">{p.brand}</span>}
        {p.unit && <span className="pc-size">{p.unit}</span>}
      </div>
      <div className="pc-price">
        <span className="now">{dollars(p.price_cents)}</span>
        {out && <span className="badge out">out of stock</span>}
      </div>
      {(p.dietary_tags?.length || p.allergens?.length) && (
        <div className="pc-tags">
          {p.dietary_tags?.slice(0, 3).map((d) => (
            <span key={d} className="tag diet">{d.replace(/_/g, " ")}</span>
          ))}
          {p.allergens?.map((a) => (
            <span key={a} className="tag allergen">contains {a}</span>
          ))}
        </div>
      )}
    </div>
  );
}
