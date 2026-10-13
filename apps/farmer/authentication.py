from rest_framework_simplejwt.authentication import JWTAuthentication

class MobileJWTAuthentication(JWTAuthentication):
    def get_raw_token(self, header):
        # Default behavior splits by space and expects ['Bearer', '<token>']
        parts = header.split()
        
        # If the user only sent the token (1 part), just return it directly!
        if len(parts) == 1:
            return parts[0]
            
        # Otherwise, fall back to default behavior (e.g. if they sent 'Bearer <token>')
        return super().get_raw_token(header)
