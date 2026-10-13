import { useConversation } from "../../store/conversation";
import { ProductCard } from "../ProductCard";

/** LIVE — search_products / get_product_variants. */
export function ProductsPanel() {
  const products = useConversation((s) => s.products);
  const variants = useConversation((s) => s.variants);

  const showVariants = variants.length > 0;
  const items = showVariants ? variants : products;

  if (items.length === 0) {
    return (
      <div className="panel empty">
        Ask Aisle to find something — “show me gluten-free pasta”.
      </div>
    );
  }

  return (
    <div className="panel">
      <h3 className="panel-title">
        {showVariants ? "Compare variants" : "Search results"}
      </h3>
      <div className="card-grid">
        {items.map((p) => (
          <ProductCard key={p.product_id} p={p} />
        ))}
      </div>
    </div>
  );
}
