from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def profile(request):
    user = request.user
    profile_data = {}
    if hasattr(user, 'profile'):
        p = user.profile
        profile_data = {
            'avatar_url': request.build_absolute_uri(p.avatar.url) if p.avatar else None,
            'bio': getattr(p, 'bio', ''),
        }
    return Response({
        'id': user.pk,
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'is_staff': user.is_staff,
        **profile_data,
    })
