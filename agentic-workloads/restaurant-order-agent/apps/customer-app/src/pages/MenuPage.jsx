import { useState, useEffect } from 'react'
import { Search } from 'lucide-react'
import { getMenu, addToCart } from '../api'
import MenuCard from '../components/MenuCard'

const CUISINES = ['All', 'Indian', 'Italian', 'Chinese', 'Japanese', 'Mexican', 'Thai', 'Burgers', 'Desserts', 'Beverages']

export default function MenuPage({ onAddToCart, loggedIn, onNeedLogin }) {
  const [items, setItems] = useState([])
  const [filtered, setFiltered] = useState([])
  const [search, setSearch] = useState('')
  const [dietary, setDietary] = useState('')
  const [cuisine, setCuisine] = useState('All')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchMenu()
  }, [dietary])

  const fetchMenu = async () => {
    setLoading(true)
    try {
      const data = await getMenu(dietary || undefined)
      const menuItems = Array.isArray(data) ? data : data.items || data.menu || []
      setItems(menuItems)
    } catch (err) {
      console.error('Failed to load menu:', err)
    }
    setLoading(false)
  }

  useEffect(() => {
    let result = items
    if (cuisine !== 'All') {
      result = result.filter(i => i.category?.toLowerCase() === cuisine.toLowerCase())
    }
    if (search) {
      result = result.filter(i =>
        i.name?.toLowerCase().includes(search.toLowerCase()) ||
        i.description?.toLowerCase().includes(search.toLowerCase()) ||
        i.category?.toLowerCase().includes(search.toLowerCase())
      )
    }
    setFiltered(result)
  }, [search, items, cuisine])

  const handleAdd = async (item) => {
    if (!loggedIn) {
      onNeedLogin()
      return
    }
    try {
      await addToCart(item.id || item.item_id, 1)
      onAddToCart()
    } catch (err) {
      console.error('Add to cart failed:', err)
    }
  }

  return (
    <div>
      {/* Hero Banner */}
      <div className="bg-gradient-to-r from-orange-500 to-orange-400 rounded-2xl p-8 mb-6 text-white">
        <h1 className="text-3xl font-bold mb-2">Hungry?</h1>
        <p className="text-orange-100">Order from 30+ dishes across 8 cuisines, delivered to your door.</p>
      </div>

      {/* Search */}
      <div className="relative mb-4">
        <Search className="absolute left-3 top-2.5 w-5 h-5 text-gray-400" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search for dishes, cuisines..."
          className="w-full pl-10 pr-4 py-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-orange-500 focus:border-transparent outline-none"
        />
      </div>

      {/* Cuisine Tabs */}
      <div className="flex gap-2 overflow-x-auto pb-3 mb-4 scrollbar-hide">
        {CUISINES.map((c) => (
          <button
            key={c}
            onClick={() => setCuisine(c)}
            className={`px-4 py-2 rounded-xl text-sm font-medium border whitespace-nowrap transition ${
              cuisine === c
                ? 'bg-orange-500 text-white border-orange-500'
                : 'bg-white text-gray-600 border-gray-200 hover:border-orange-300'
            }`}
          >
            {c}
          </button>
        ))}
      </div>

      {/* Dietary Filters */}
      <div className="flex gap-2 mb-6">
        {['', 'veg', 'vegan', 'non-veg'].map((f) => (
          <button
            key={f}
            onClick={() => setDietary(f)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition ${
              dietary === f
                ? f === 'veg' ? 'bg-green-500 text-white border-green-500'
                : f === 'vegan' ? 'bg-green-600 text-white border-green-600'
                : f === 'non-veg' ? 'bg-red-500 text-white border-red-500'
                : 'bg-orange-500 text-white border-orange-500'
                : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300'
            }`}
          >
            {f || 'All'}
          </button>
        ))}
      </div>

      {/* Menu Grid */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="bg-white rounded-xl h-72 animate-pulse border" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          <p className="text-lg">No items found</p>
          <p className="text-sm mt-1">Try adjusting your filters</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filtered.map((item) => (
            <MenuCard key={item.id || item.item_id} item={item} onAdd={handleAdd} />
          ))}
        </div>
      )}
    </div>
  )
}
