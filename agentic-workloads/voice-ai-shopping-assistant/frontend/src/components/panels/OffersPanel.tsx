import { useConversation } from "../../store/conversation";

function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

/** LIVE — UC5 specials. Fed by the get_offers tool (joins products ⨝ specials). */
export function OffersPanel() {
  const offers = useConversation((s) => s.offers);

  if (offers.length === 0) {
    return (
      <div className="panel empty">
        Ask Aisle “what’s on special?” to see this week’s deals.
      </div>
    );
  }

  return (
    <div className="panel">
      <h3 className="panel-title">On special</h3>
      <div className="card-grid">
        {offers.map((o) => (
          <div key={o.product_id} className="product-card on-special">
            {o.image_url && (
              <div className="pc-img">
                <img src={o.image_url} alt="" loading="lazy" />
              </div>
            )}
            <div className="pc-head">
              <span className="pc-name">{o.name}</span>
              {o.special_type && (
                <span className="badge special">
                  {o.special_type === "half_price" ? "½ price" : "special"}
                </span>
              )}
            </div>
            <div className="pc-sub">
              {o.brand && <span className="pc-brand">{o.brand}</span>}
              {o.unit && <span className="pc-size">{o.unit}</span>}
            </div>
            <div className="pc-price">
              <span className="now">{dollars(o.special_price_cents)}</span>
              <span className="was">{dollars(o.was_price_cents)}</span>
              {typeof o.pct_below_usual === "number" && o.pct_below_usual > 0 && (
                <span className="save">−{o.pct_below_usual}%</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
