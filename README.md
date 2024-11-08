# Brivo CSV upload webapp

## Prerequisites
- Python 3.x
- virtualenv package
- Docker (optional)

## Brivo api authentication note


The Brivo API uses 3-legged OAuth2 authentication to connect applications securely. To use the API, users need to log in to the Brivo website actively. After the initial login, the server can refresh the token credential without direct user involvement.

Here is a high-level view of the flow
- Initially, the user's web browser is redirected to Brivo's website, where they log in and authorize the app. 
- Once authenticated, they are redirected back to a preconfigured URL (any browser-accessible URL will work—cloud, local, http, or https) with an authorization code. 
- The server then exchanges this code for an API token, granting secure access to the Brivo API on behalf of the user.

## Brivo setup

1. Create a developer account at developer.brivo.com.
    1. This will provide you with an API key that you will need later.
    1. Obtain sandbox access to access.brivo.com.
    1. If you don't receive an email with your sandbox access credentials, contact Brivo support.
1. Create an application to obtain a client ID and client secret:
    1. Log in to Access.
    1. Navigate to Configuration > Integrations.
    1. Click Generate API Token.
    1. Set the application type to 3-legged authentication.
    1. Set the redirect URI to http://localhost:8080/oauth_callback.
    1. Submit the form to get your client ID and client secret.

## Setup
1. Create a Virtual Environment
    ```
    virtualenv -p python3 venv 
    source venv/bin/activate
    ```
1. Install Dependencies
    ```
    pip install -r requirements.txt
    ```
1. Configure Environment Variables
Set the following environment variables:
    ```
    export BRIVO_APIKEY="your_api_key"
    export BRIVO_CLIENT_ID="your_client_id"
    export BRIVO_CLIENT_SECRET="your_client_secret"
    export BRIVO_REDIRECT_URI="http://localhost:8080/oauth_callback"
    ```
1. Run Tests
    1. Just tests
    ```
    PYTHONPATH=. pytest
    ```
    1. Coverage report
    ```
    PYTHONPATH=. pytest  --cov=app --cov-report html
    ```
1. Build&Run Docker Container
    1. Development mode
    ```
    PYTHONPATH=. python app/webapp.py --host 127.0.0.1 --port 8080 --debug
    ```
    1. Production mode
    ```
    docker build -t your_image_name .

    docker run \
        -e BRIVO_APIKEY=$BRIVO_APIKEY \
        -e BRIVO_CLIENT_ID=$BRIVO_CLIENT_ID \
        -e BRIVO_CLIENT_SECRET=$BRIVO_CLIENT_SECRET \
        -e BRIVO_REDIRECT_URI=$BRIVO_REDIRECT_URI \
        --rm --name brivo-app -p 8080:8080 \
        your_image_name
    ```
