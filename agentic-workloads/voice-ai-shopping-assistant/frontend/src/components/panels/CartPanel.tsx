import { useConversation } from "../../store/conversation";

function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

/**
 * LIVE — the cart is the curated subset moved from the grocery list
 * (add_to_cart / get_cart, DB-backed), plus the create_order confirmation.
 */
export function CartPanel() {
  const cart = useConversation((s) => s.cart);
  const order = useConversation((s) => s.order);
  const setActiveTab = useConversation((s) => s.setActiveTab);

  const items = cart?.items ?? [];
  const total =
    cart?.subtotal_cents ??
    items.reduce((sum, i) => sum + i.price_cents * i.qty, 0);

  return (
    <div className="panel">
      <h3 className="panel-title">Your cart</h3>

      {items.length === 0 ? (
        <div className="empty">
          Nothing here yet — add items from your{" "}
          <button className="linklike" onClick={() => setActiveTab("list")}>
            grocery list
          </button>{" "}
          to build the cart, then check out.
        </div>
      ) : (
        <>
          <ul className="cart-list">
            {items.map((i, idx) => (
              <li key={`${i.product_id}-${idx}`} className="cart-row">
                <span className="qty">{i.qty}×</span>
                <span className="name">{i.name}</span>
                <span className="price">
                  {i.price_cents > 0 ? dollars(i.price_cents * i.qty) : "—"}
                </span>
              </li>
            ))}
          </ul>
          <div className="cart-total">
            <span>Subtotal</span>
            <span className="amt">{total > 0 ? dollars(total) : "—"}</span>
          </div>
          {!order && (
            <p className="cart-hint">
              Say “check out” or “place my pickup order” to finish.
            </p>
          )}
        </>
      )}

      {order && (
        <div className={`order-card ${order.status ?? ""}`}>
          <div className="order-head">
            <span className="oc-title">Pickup order</span>
            <span className="oc-status">{order.status ?? "placed"}</span>
          </div>
          {order.pickup_code && (
            <div className="oc-code">
              Pickup code <strong>{order.pickup_code}</strong>
            </div>
          )}
          {order.pickup_time && <div className="oc-eta">Pickup {order.pickup_time}</div>}
          {typeof order.total_cents === "number" && (
            <div className="oc-total">{dollars(order.total_cents)}</div>
          )}
          {order.payment_id && <div className="oc-pay">Paid · {order.payment_id.slice(0, 8)}</div>}
        </div>
      )}
    </div>
  );
}
