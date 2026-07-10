import { useConversation } from "../../store/conversation";

/** MOCK (seeded) — UC4 dietary + preferred-brand personalisation. Preview. */
export function ProfilePanel() {
  const profile = useConversation((s) => s.profile);
  return (
    <div className="panel">
      <h3 className="panel-title">
        {profile.display_name}’s preferences <span className="preview-pill">preview</span>
      </h3>

      <div className="profile-block">
        <span className="pb-label">Dietary</span>
        <div className="chips">
          {profile.dietary.map((d) => (
            <span key={d} className="chip diet">{d}</span>
          ))}
        </div>
      </div>

      <div className="profile-block">
        <span className="pb-label">Avoids</span>
        <div className="chips">
          {profile.avoid_allergens.map((a) => (
            <span key={a} className="chip allergen">{a}</span>
          ))}
        </div>
      </div>

      <div className="profile-block">
        <span className="pb-label">Preferred brands</span>
        <div className="chips">
          {profile.preferred_brands.map((b) => (
            <span key={b.category} className="chip brand">
              {b.brand} <em>{b.category}</em>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
