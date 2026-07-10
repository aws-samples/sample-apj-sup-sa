import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    Gateway interceptor that propagates Authorization header to targets.
    
    For REQUEST: Extracts auth header and passes it to target
    For RESPONSE: Passes response through unchanged
    """
    mcp_data = event.get('mcp', {})
    
    # Check if RESPONSE interceptor
    if 'gatewayResponse' in mcp_data and mcp_data['gatewayResponse'] is not None:
        logger.info("RESPONSE interceptor - passing through")
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayResponse": {
                    "body": mcp_data.get('gatewayResponse', {}).get('body', {}),
                    "statusCode": mcp_data.get('gatewayResponse', {}).get('statusCode', 200)
                }
            }
        }
    
    # REQUEST interceptor
    gateway_request = mcp_data.get('gatewayRequest', {})
    request_body = gateway_request.get('body', {})
    headers = gateway_request.get('headers', {})
    
    mcp_method = request_body.get('method', 'unknown')
    logger.info(f"REQUEST interceptor - method: {mcp_method}")
    
    # Extract Authorization header (case-insensitive)
    auth_header = None
    for key, value in headers.items():
        if key.lower() == 'authorization':
            auth_header = value
            break
    
    # Build response with headers to propagate
    response_headers = {}
    if auth_header:
        response_headers['Authorization'] = auth_header
        logger.info("Propagating Authorization header to target")
    
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "body": request_body,
                "headers": response_headers
            }
        }
    }
