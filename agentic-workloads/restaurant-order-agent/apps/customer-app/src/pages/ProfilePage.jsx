import { useState, useEffect } from 'react'
import { User, MapPin, Save, Leaf } from 'lucide-react'
import { getProfile, isAuthenticated } from '../api'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

export default function ProfilePage() {
  const [profile, setProfile] = useState({ name: '', address: '', dietary_preference: '' })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetchProfile()
  }, [])

  const fetchProfile = async () => {
    try {
      const data = await getProfile()
      setProfile(data)
    } catch (err) {
      console.error('Profile load failed:', err)
    }
    setLoading(false)
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const token = localStorage.getItem('auth_token')
      const res = await fetch(`${API_BASE}/profile`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify(profile),
      })
      if (res.ok) {
        setSaved(true)
        setTimeout(() => setSaved(false), 2000)
      }
    } catch (err) {
      console.error('Save failed:', err)
    }
    setSaving(false)
  }

  if (loading) return <div className="text-center py-12 text-gray-500">Loading profile...</div>

  return (
    <div className="max-w-lg mx-auto">
      <h1 className="text-2xl font-bold text-gray-800 mb-6">Your Profile</h1>

      <div className="bg-white rounded-xl border p-6 space-y-5">
        {/* Phone (read-only) */}
        <div>
          <label className="text-sm font-medium text-gray-600 mb-1 block">Phone Number</label>
          <div className="px-4 py-2.5 bg-gray-50 rounded-xl text-gray-700 text-sm">
            {profile.phone_number || localStorage.getItem('phone_number') || 'Not set'}
          </div>
        </div>

        {/* Name */}
        <div>
          <label className="text-sm font-medium text-gray-600 mb-1 block flex items-center gap-1">
            <User className="w-4 h-4" /> Name
          </label>
          <input
            type="text"
            value={profile.name || ''}
            onChange={(e) => setProfile({ ...profile, name: e.target.value })}
            placeholder="Your name"
            className="w-full px-4 py-2.5 border border-gray-200 rounded-xl text-sm focus:ring-2 focus:ring-orange-500 focus:border-transparent outline-none"
          />
        </div>

        {/* Delivery Address */}
        <div>
          <label className="text-sm font-medium text-gray-600 mb-1 block flex items-center gap-1">
            <MapPin className="w-4 h-4" /> Delivery Address
          </label>
          <textarea
            value={profile.address || ''}
            onChange={(e) => setProfile({ ...profile, address: e.target.value })}
            placeholder="Enter your delivery address"
            rows={3}
            className="w-full px-4 py-2.5 border border-gray-200 rounded-xl text-sm focus:ring-2 focus:ring-orange-500 focus:border-transparent outline-none resize-none"
          />
        </div>

        {/* Dietary Preference */}
        <div>
          <label className="text-sm font-medium text-gray-600 mb-1 block flex items-center gap-1">
            <Leaf className="w-4 h-4" /> Dietary Preference
          </label>
          <div className="flex gap-2">
            {['', 'veg', 'vegan', 'non-veg'].map(opt => (
              <button
                key={opt}
                onClick={() => setProfile({ ...profile, dietary_preference: opt })}
                className={`px-4 py-2 rounded-xl text-sm font-medium border transition ${
                  profile.dietary_preference === opt
                    ? 'bg-orange-500 text-white border-orange-500'
                    : 'bg-white text-gray-600 border-gray-200 hover:border-orange-300'
                }`}
              >
                {opt || 'Any'}
              </button>
            ))}
          </div>
        </div>

        {/* Save Button */}
        <button
          onClick={handleSave}
          disabled={saving}
          className="w-full py-3 bg-orange-500 text-white font-semibold rounded-xl hover:bg-orange-600 disabled:opacity-50 transition flex items-center justify-center gap-2"
        >
          <Save className="w-4 h-4" />
          {saving ? 'Saving...' : saved ? '✓ Saved!' : 'Save Profile'}
        </button>
      </div>
    </div>
  )
}
