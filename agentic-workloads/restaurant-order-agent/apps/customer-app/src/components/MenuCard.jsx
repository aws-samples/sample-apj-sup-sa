import { useState } from 'react'
import { Plus, Check } from 'lucide-react'

export default function MenuCard({ item, onAdd }) {
  const [added, setAdded] = useState(false)
  const [count, setCount] = useState(0)

  const dietaryColors = {
    veg: 'border-green-500 text-green-600',
    vegan: 'border-green-600 text-green-700',
    'non-veg': 'border-red-500 text-red-600',
  }

  const handleAdd = () => {
    onAdd(item)
    setCount(c => c + 1)
    setAdded(true)
    setTimeout(() => setAdded(false), 1500)
  }

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm hover:shadow-md transition overflow-hidden group">
      {/* Image */}
      <div className="h-44 overflow-hidden relative">
        {item.image ? (
          <img
            src={item.image}
            alt={item.name}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full bg-gradient-to-br from-orange-100 to-orange-50 flex items-center justify-center">
            <span className="text-4xl">🍽️</span>
          </div>
        )}
        {item.category && (
          <span className="absolute top-2 left-2 bg-black/60 text-white text-xs px-2 py-0.5 rounded-full">
            {item.category}
          </span>
        )}
      </div>

      <div className="p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              {item.dietary_flag && (
                <span className={`text-xs font-medium border px-1.5 py-0.5 rounded ${dietaryColors[item.dietary_flag] || ''}`}>
                  {item.dietary_flag}
                </span>
              )}
            </div>
            <h3 className="font-semibold text-gray-800 text-sm leading-tight truncate">{item.name}</h3>
            {item.description && (
              <p className="text-xs text-gray-500 mt-1 line-clamp-2">{item.description}</p>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between mt-3">
          <span className="text-base font-bold text-gray-800">₹{item.price}</span>
          <button
            onClick={handleAdd}
            className={`flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
              added
                ? 'bg-green-500 text-white border border-green-500'
                : count > 0
                ? 'bg-orange-500 text-white border border-orange-500'
                : 'border border-orange-500 text-orange-500 hover:bg-orange-50'
            }`}
          >
            {added ? (
              <>
                <Check className="w-4 h-4" />
                Added
              </>
            ) : count > 0 ? (
              <>
                <Plus className="w-4 h-4" />
                {count} added
              </>
            ) : (
              <>
                <Plus className="w-4 h-4" />
                ADD
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
