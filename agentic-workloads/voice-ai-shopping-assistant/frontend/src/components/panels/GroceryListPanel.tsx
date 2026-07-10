import { useConversation, itemKey, itemLabel, itemQty } from "../../store/conversation";

const STATUS_LABEL: Record<string, string> = {
  active: "to buy",
  have: "have it",
  out_of_stock: "out of stock",
};

/**
 * LIVE — UC1 grocery list. Everything the shopper says lands here first
 * (update_grocery_list tool_result). The human then moves items into the CART
 * with "Add to cart" — list → cart → checkout.
 */
export function GroceryListPanel() {
  const list = useConversation((s) => s.list);
  const movedToCart = useConversation((s) => s.movedToCart);
  const moveItemToCart = useConversation((s) => s.moveItemToCart);
  const setActiveTab = useConversation((s) => s.setActiveTab);

  if (list.length === 0) {
    return (
      <div className="panel empty">
        Your list is empty — say “I need to buy milk” or “add bread to my list”.
      </div>
    );
  }

  const buyable = list.filter((i) => (i.status ?? "active") !== "have");
  const allMoved =
    buyable.length > 0 && buyable.every((i) => movedToCart[itemKey(i)]);

  return (
    <div className="panel">
      <div className="panel-title-row">
        <h3 className="panel-title">Your grocery list</h3>
        <button
          className="btn small primary"
          disabled={allMoved}
          onClick={() => {
            buyable.forEach((i) => moveItemToCart(i));
            setActiveTab("cart");
          }}
        >
          {allMoved ? "All in cart" : "Add all to cart"}
        </button>
      </div>

      <ul className="grocery-list">
        {list.map((i) => {
          const status = i.status ?? "active";
          const key = itemKey(i);
          const moved = !!movedToCart[key];
          const canBuy = status !== "have";
          return (
            <li key={key} className={`gl-row status-${status}`}>
              <span className="gl-name">{itemLabel(i)}</span>
              {itemQty(i) > 1 && <span className="gl-qty">×{itemQty(i)}</span>}
              <span className={`gl-status ${status}`}>
                {STATUS_LABEL[status] ?? status}
              </span>
              {canBuy && (
                <button
                  className={`btn tiny ${moved ? "muted" : ""}`}
                  disabled={moved || status === "out_of_stock"}
                  onClick={() => moveItemToCart(i)}
                  title={
                    status === "out_of_stock"
                      ? "Out of stock"
                      : moved
                      ? "In cart"
                      : "Add to cart"
                  }
                >
                  {moved ? "✓ in cart" : "+ cart"}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
