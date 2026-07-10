import { useMemo } from "react";
import { useConversation, itemLabel } from "../../store/conversation";
import { suggestRecipes } from "../../lib/recipe-engine";

/**
 * Recipe ideas auto-generated from what the shopper is engaging with — the items
 * on their live grocery LIST plus recently SEARCHED products. Updates as they add
 * things by voice. (No deployed recipe tool/table; derived client-side.)
 */
export function RecipePanel() {
  const list = useConversation((s) => s.list);
  const products = useConversation((s) => s.products);

  const recipes = useMemo(() => {
    const names = [
      ...list.map((i) => itemLabel(i)),
      ...products.map((p) => p.name),
    ];
    return suggestRecipes(names, 4);
  }, [list, products]);

  if (recipes.length === 0) {
    return (
      <div className="panel empty">
        Add items to your list (or search for products) and I’ll suggest meals you
        can make with them.
      </div>
    );
  }

  return (
    <div className="panel">
      <h3 className="panel-title">Ideas from your list</h3>
      <div className="card-grid">
        {recipes.map((r) => (
          <div key={r.name} className="recipe-card">
            <div className="rc-head">
              <span className="rc-name">{r.name}</span>
              <span className="badge special">serves {r.servings}</span>
            </div>
            {r.matched.length > 0 && (
              <div className="rc-uses">uses your {r.matched.slice(0, 3).join(", ")}</div>
            )}
            <div className="rc-tags">
              {r.tags.map((t) => (
                <span key={t} className="tag diet">{t}</span>
              ))}
            </div>
            <div className="rc-ingredients">{r.ingredients.join(" · ")}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
