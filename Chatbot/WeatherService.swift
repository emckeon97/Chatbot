import Foundation
import CoreLocation

/// Free weather via Open-Meteo (no API key). Usage: "weather" or "weather in Charlotte".
final class WeatherService: NSObject, ObservableObject {
    static let shared = WeatherService()
    private let geocoder = CLGeocoder()

    /// Parse "weather"-type requests. Returns a location string, or "" for current location.
    func parseQuery(_ text: String) -> String? {
        let low = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        guard low.hasPrefix("weather") || low.contains("what's the weather") || low.contains("whats the weather") else { return nil }
        // "weather in Charlotte" -> "Charlotte"
        if let range = low.range(of: "weather in ") {
            return String(low[range.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if let range = low.range(of: "weather for ") {
            return String(low[range.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return ""  // current location
    }

    func fetch(location: String) async -> String {
        do {
            let coords: (Double, Double)
            let placeName: String
            if location.isEmpty {
                // Use IP-based approx via Open-Meteo geocoding of nothing — fall back to Charlotte
                return await fetchCoords(lat: 35.2271, lon: -80.8431, name: "Charlotte")
            } else {
                let placemarks = try await geocoder.geocodeAddressString(location)
                guard let p = placemarks.first, let loc = p.location else {
                    return "Couldn't find that place."
                }
                coords = (loc.coordinate.latitude, loc.coordinate.longitude)
                placeName = p.locality ?? p.name ?? location
                return await fetchCoords(lat: coords.0, lon: coords.1, name: placeName)
            }
        } catch {
            return "Weather lookup failed."
        }
    }

    private func fetchCoords(lat: Double, lon: Double, name: String) async -> String {
        let urlStr = "https://api.open-meteo.com/v1/forecast?latitude=\(lat)&longitude=\(lon)&current=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min&temperature_unit=fahrenheit&timezone=auto&forecast_days=1"
        guard let url = URL(string: urlStr),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let current = json["current"] as? [String: Any],
              let temp = current["temperature_2m"] as? Double,
              let code = current["weather_code"] as? Int else {
            return "Couldn't get the weather right now."
        }
        var desc = "Clear"
        switch code {
        case 1...3: desc = "Partly cloudy"
        case 45, 48: desc = "Foggy"
        case 51...57: desc = "Drizzle"
        case 61...67: desc = "Rain"
        case 71...77: desc = "Snow"
        case 80...82: desc = "Showers"
        case 95...99: desc = "Thunderstorms"
        default: break
        }
        var hi = "", lo = ""
        if let daily = json["daily"] as? [String: Any],
           let maxs = daily["temperature_2m_max"] as? [Double], let first = maxs.first,
           let mins = daily["temperature_2m_min"] as? [Double], let firstMin = mins.first {
            hi = " High \(Int(first))°F, low \(Int(firstMin))°F."
        }
        return "\(name): \(Int(temp))°F, \(desc).\(hi)"
    }
}
