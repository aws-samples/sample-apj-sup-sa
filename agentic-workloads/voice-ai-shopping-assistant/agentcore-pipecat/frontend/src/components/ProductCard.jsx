import React from 'react';

const dollars = (cents) =>
  typeof cents === 'number' ? `$${(cents / 100).toFixed(2)}` : '';

export default function ProductCard({ product }) {
  const special = product.special || null;
  const onSpecial = special || product.on_special;
  const specialPrice = special?.special_price_cents ?? product.special_price_cents;
  const wasPrice = special?.was_price_cents ?? product.price_cents;
  const showPrice = onSpecial && specialPrice ? specialPrice : product.price_cents;

  return (
    <div className={`product-card ${product.in_stock === false ? 'oos' : ''}`}>
      <div className="product-image-wrap">
        {product.image_url ? (
          <img src={product.image_url} alt={product.name} loading="lazy" />
        ) : (
          <div className="product-image-placeholder">🛒</div>
        )}
        {onSpecial && <span className="badge badge-special">Special</span>}
        {product.in_stock === false && <span className="badge badge-oos">Out of stock</span>}
      </div>

      <div className="product-body">
        {product.brand && <p className="product-brand">{product.brand}</p>}
        <p className="product-name">{product.name}</p>
        {product.unit && <p className="product-unit">{product.unit}</p>}

        <div className="product-price-row">
          <span className="product-price">{dollars(showPrice)}</span>
          {onSpecial && specialPrice && wasPrice > specialPrice && (
            <span className="product-was">{dollars(wasPrice)}</span>
          )}
        </div>

        {Array.isArray(product.allergens) && product.allergens.length > 0 && (
          <div className="chips">
            {product.allergens.slice(0, 3).map((a) => (
              <span key={a} className="chip chip-allergen">{a}</span>
            ))}
          </div>
        )}
        {Array.isArray(product.dietary_tags) && product.dietary_tags.length > 0 && (
          <div className="chips">
            {product.dietary_tags.slice(0, 3).map((d) => (
              <span key={d} className="chip chip-dietary">{d.replace(/_/g, ' ')}</span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
