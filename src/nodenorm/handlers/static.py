from biothings.web.handlers import BaseHandler
from biothings_annotator.application.structure import WEB_APP_DIRECTORY


class StaticFrontendHandler(BaseHandler):

    async def get(self) -> file:
        """Loads the main index for our openapi swagger ui.

        The local file must be relative given we have both local
        development and docker development

        The WEB_APP_DIRECTORY is relative to the webapp directory
        in the biothings-annotator repository

        The DOCKER_WEB_APP_DIRECTORY is /webapp at the root of the
        docker container
        """
        html_index_file = WEB_APP_DIRECTORY.joinpath("index.html")
        return await file(html_index_file)


class FaviconView(BaseHandler):

    async def get(self, _: Request) -> file:
        """Loads the favicon file when we view our swagger ui.

        This should eliminate logs unable to find the favicon clogging
        our stdout for the annotator
        """
        favicon_file_name = "favicon-32x32.png"
        if DOCKER_WEB_APP_DIRECTORY.exists():
            favicon_file = DOCKER_WEB_APP_DIRECTORY / "swaggerui" / favicon_file_name
        else:
            favicon_file = WEB_APP_DIRECTORY / "swaggerui" / favicon_file_name
        return await file(favicon_file)
