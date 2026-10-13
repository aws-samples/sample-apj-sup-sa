import React from 'react';

const dollars = (cents) =>
  typeof cents === 'number' ? `$${(cents / 100).toFixed(2)}` : '$0.00';

export default function CartPanel({ cart, order }) {
  const items = cart?.items || [];

  return (
    <aside className="cart-panel">
      <h2 className="cart-title">Your cart</h2>

      {items.length === 0 ? (
        <p className="cart-empty">Empty — ask Aisle to add something.</p>
      ) : (
        <ul className="cart-list">
          {items.map((it) => (
            <li key={it.product_id || it.name} className="cart-item">
              <span className="cart-item-qty">{it.qty}×</span>
              <span className="cart-item-name">{it.name}</span>
              <span className="cart-item-price">{dollars(it.price_cents * it.qty)}</span>
            </li>
          ))}
        </ul>
      )}

      {items.length > 0 && (
        <div className="cart-subtotal">
          <span>Subtotal</span>
          <span>{dollars(cart?.subtotal_cents)}</span>
        </div>
      )}

      {order && (
        <div className="order-confirm">
          <p className="order-confirm-title">Order placed ✓</p>
          <p className="order-confirm-row">Total: {dollars(order.total_cents)}</p>
          {order.pickup_code && (
            <p className="order-confirm-row">Pickup code: <strong>{order.pickup_code}</strong></p>
          )}
        </div>
      )}
    </aside>
  );
}
