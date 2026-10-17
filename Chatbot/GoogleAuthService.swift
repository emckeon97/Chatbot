import Foundation
import GoogleSignIn
import UIKit

/// Google Sign-In auth only. No Gmail API calls.
/// Requires GoogleSignIn SDK added via SPM in Xcode.
/// Add the reversed client ID as a URL scheme in Info.plist.
@MainActor
final class GoogleAuthService: ObservableObject {
    static let shared = GoogleAuthService()

    @Published private(set) var isSignedIn = false
    @Published private(set) var userEmail: String?
    @Published var errorMessage: String?

    private(set) var accessToken: String?

    private init() {
        let config = GIDConfiguration(clientID: "294125895152-cmamrfum6a6eu44gp3nd52h7amtl9q08.apps.googleusercontent.com")
        GIDSignIn.sharedInstance.configuration = config
    }

    func signIn() {
        guard let rootVC = UIApplication.shared.connectedScenes
            .compactMap({ $0 as? UIWindowScene })
            .flatMap({ $0.windows })
            .first(where: { $0.isKeyWindow })?.rootViewController else {
            errorMessage = "Could not find presenting view controller."
            return
        }
        GIDSignIn.sharedInstance.signIn(
            withPresenting: rootVC,
            hint: nil,
            additionalScopes: [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send"
            ]
        ) { [weak self] result, error in
            Task { @MainActor in
                if let error {
                    self?.errorMessage = error.localizedDescription
                    return
                }
                guard let user = result?.user else {
                    self?.errorMessage = "Sign-in failed."
                    return
                }
                self?.accessToken = user.accessToken.tokenString
                self?.userEmail = user.profile?.email
                self?.isSignedIn = true
            }
        }
    }

    func signOut() {
        GIDSignIn.sharedInstance.signOut()
        accessToken = nil
        userEmail = nil
        isSignedIn = false
    }

    /// Restore previous sign-in silently.
    func restoreSignIn() {
        GIDSignIn.sharedInstance.restorePreviousSignIn { [weak self] user, error in
            Task { @MainActor in
                if let user {
                    self?.accessToken = user.accessToken.tokenString
                    self?.userEmail = user.profile?.email
                    self?.isSignedIn = true
                }
            }
        }
    }
}
