import requests
import re
from garmin_uploader import logger

URL_HOSTNAME = 'https://connect.garmin.com/modern/auth/hostname'
URL_LOGIN = 'https://sso.garmin.com/sso/login'
URL_POST_LOGIN = 'https://connect.garmin.com/modern/'
URL_CHECK_LOGIN = 'https://connect.garmin.com/user/username'
URL_SESSION = 'https://connect.garmin.com/legacy/session'
URL_HOST_SSO = 'sso.garmin.com'
URL_HOST_CONNECT = 'connect.garmin.com'
URL_UPLOAD = 'https://connect.garmin.com/modern/proxy/upload-service/upload'
URL_ACTIVITY_BASE = 'https://connect.garmin.com/modern/proxy/activity-service/activity'  # noqa
URL_ACTIVITY_TYPE = 'https://connect.garmin.com/proxy/activity-service-1.2/json/type'  # noqa
URL_ACTIVITY_TYPES = 'https://connect.garmin.com/proxy/activity-service-1.2/json/activity_types'  # noqa


class GarminAPIException(Exception):
    """
    An Exception occured in Garmin API
    """


class GarminAPI:
    """
    Low level Garmin Connect api connector
    """
    activity_types = None

    # This strange header is needed to get auth working
    common_headers = {
        'NK': 'NT',
    }

    def authenticate(self, username, password):
        """
        That's where the magic happens !
        Try to mimick a browser behavior trying to login
        on Garmin Connect as closely as possible
        Outputs a Requests session, loaded with precious cookies
        """
        # Use a valid Browser user agent
        # TODO: use several UA picked randomly
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:48.0) Gecko/20100101 Firefox/50.0',  # noqa
        })

        # Request sso hostname
        sso_hostname = None
        resp = session.get(URL_HOSTNAME)
        if not resp.ok:
            raise Exception('Invalid SSO first request status code {}'.format(resp.status_code))  # noqa
        sso_hostname = resp.json().get('host')

        # Load login page to get login ticket
        # Full parameters from Firebug, we have to maintain
        # Fuck this shit.
        # Who needs mandatory urls in a request parameters !
        params = {
            'clientId': 'GarminConnect',
            'connectLegalTerms': 'true',
            'consumeServiceTicket': 'false',
            'createAccountShown': 'true',
            'cssUrl': 'https://static.garmincdn.com/com.garmin.connect/ui/css/gauth-custom-v1.2-min.css',  # noqa
            'displayNameShown': 'false',
            'embedWidget': 'false',
            'gauthHost': 'https://sso.garmin.com/sso',
            'generateExtraServiceTicket': 'false',
            'globalOptInChecked': 'false',
            'globalOptInShown': 'false',
            'id': 'gauth-widget',
            'initialFocus': 'true',
            'locale': 'fr',
            'openCreateAccount': 'false',
            'privacyStatementUrl': '//connect.garmin.com/fr-FR/privacy/',
            'redirectAfterAccountCreationUrl': 'https://connect.garmin.com/modern/',  # noqa
            'redirectAfterAccountLoginUrl': 'https://connect.garmin.com/modern/',  # noqa
            'rememberMeChecked': 'false',
            'rememberMeShown': 'true',
            'service': 'https://connect.garmin.com/modern/',
            'source': 'https://connect.garmin.com/fr-FR/signin',
            'usernameShown': 'false',
            'webhost': sso_hostname
        }
        res = session.get(URL_LOGIN, params=params)
        if res.status_code != 200:
            raise Exception('No login form')

        # Get the login ticket value
        regex = '<input\s+type="hidden"\s+name="lt"\s+value="(?P<lt>\w+)"\s+/>'
        res = re.search(regex, res.text)
        if not res:
            raise Exception('No login ticket')
        login_ticket = res.group('lt')
        logger.debug('Found login ticket %s', login_ticket)

        # Login/Password with login ticket
        data = {
          # All parameters are needed
          '_eventId': 'submit',
          'displayNameRequired': 'false',
          'embed': 'true',
          'lt': login_ticket,
          'username': username,
          'password': password,
        }
        headers = {
          'Host': URL_HOST_SSO,
        }
        res = session.post(URL_LOGIN, params=params, data=data,
                           headers=headers)
        if res.status_code != 200:
            raise Exception('Authentification failed.')

        # Check we have sso guid in cookies
        if 'GARMIN-SSO-GUID' not in session.cookies:
            raise Exception('Missing Garmin auth cookie')

        # Try to find the full post login url in response
        regex = 'var response_url(\s+)= \'.*?ticket=(?P<ticket>[\w\-]+)\''
        params = {}
        matches = re.search(regex, res.text)
        if not matches:
            raise Exception('Missing service ticket')
        params['ticket'] = matches.group('ticket')
        logger.debug('Found service ticket {}'.format(params['ticket']))

        # Second auth step
        # Needs a service ticket from previous response
        headers = {
            'Host': URL_HOST_CONNECT,
        }
        res = session.get(URL_POST_LOGIN, params=params, headers=headers)
        if res.status_code != 200 and not res.history:
            raise Exception('Second auth step failed.')

        # Get Jsessionid
        res = session.get(URL_SESSION)
        res.raise_for_status()
        if 'JSESSIONID' not in session.cookies:
            raise Exception('Missing jsession auth cookie')

        # Check login
        res = session.get(URL_CHECK_LOGIN)
        garmin_user = res.json()
        username = garmin_user.get('username')
        if not res.ok or not username:
            raise Exception("Login check failed.")
        logger.info('Logged in as {}'.format(username))

        return session

    def upload_activity(self, session, activity):
        """
        Upload an activity on Garmin
        Support multiple formats
        """
        assert activity.id is None

        # Upload file as multipart form
        files = {
            "file": (activity.filename, activity.open()),
        }
        url = '{}/{}'.format(URL_UPLOAD, activity.extension)
        res = session.post(url, files=files, headers=self.common_headers)

        # HTTP Status can either be OK or Conflict
        if res.status_code not in (200, 201, 409):
            raise GarminAPIException('Failed to upload {}'.format(activity))

        response = res.json()['detailedImportResult']
        if len(response["successes"]) == 0:
            if len(response["failures"]) > 0:
                if response["failures"][0]["messages"][0]['code'] == 202:
                    # Activity already exists
                    return response["failures"][0]["internalId"], False
                else:
                    raise GarminAPIException(response["failures"][0]["messages"])  # noqa
            else:
                raise GarminAPIException('Unknown error: {}'.format(response))
        else:
            # Upload was successsful
            return response["successes"][0]["internalId"], True

    def set_activity_name(self, session, activity):
        """
        Update the activity name
        """
        assert activity.id is not None
        assert activity.name is not None

        url = '{}/{}'.format(URL_ACTIVITY_BASE, activity.id)
        data = {
            'activityId': activity.id,
            'activityName': activity.name,
        }
        headers = dict(self.common_headers)  # clone
        headers['X-HTTP-Method-Override'] = 'PUT'  # weird. again.
        res = session.post(url, json=data, headers=headers)
        if not res.ok:
            raise GarminAPIException('Activity name not set: {}'.format(res.content))  # noqa

    def load_activity_types(self):
        """
        Fetch valid activity types from Garmin Connect
        """
        # Only fetch once
        if self.activity_types:
            return self.activity_types

        logger.debug('Fecthing activity types')
        resp = requests.get(URL_ACTIVITY_TYPES)
        if not resp.ok:
            raise GarminAPIException('Failed to retrieve activity types')

        # Store as a clean dict, mapping keys and lower case common name
        types = resp.json()["dictionary"]
        out = [(t['key'], t['key']) for t in types]
        out += [(t['display'].lower(), t['key']) for t in types]
        out = dict(out)
        self.activity_types = out

        logger.debug('Fetched {} activity types'.format(len(self.activity_types)))  # noqa
        return self.activity_types

    def set_activity_type(self, session, activity):
        """
        Update the activity type
        """
        assert activity.id is not None
        assert activity.type is not None

        # Load the corresponding type key on Garmin Connect
        types = self.load_activity_types()
        type_key = types.get(activity.type)
        if type_key is None:
            logger.error("Activity type '{}' not valid".format(activity.type))
            return False

        url = '{}/{}'.format(URL_ACTIVITY_TYPE, activity.id)
        data = {
            'value': type_key,
        }
        res = session.post(url, data)
        if not res.ok:
            raise GarminAPIException('Activity type not set: {}'.format(res.content))  # noqa

        res = res.json()
        if "activityType" not in res or res["activityType"]["key"] != type_key:
            raise GarminAPIException('Activity type not set: {}'.format(res.content))  # noqa
