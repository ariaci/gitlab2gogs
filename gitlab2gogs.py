import click
import tarfile
import re
from git import Repo
import tempfile
from pathlib import Path
import os
import stat
import shutil
import gogs_client
from datetime import datetime
import collections


class GitlabBackup:
    def __init__(self, backupfilename, usernames, gogsbaseurl=None, gogsadmin=None):
        self.backupfile = tarfile.open(backupfilename, "r")
        self.usernames = [username.lower() for username in usernames]
        self.GitLabRepo = collections.namedtuple(
            "GitLabRepo", "bundle membership name"
        )
        if gogsbaseurl != None and gogsadmin != None:
            self.gogsbaseurl = gogsbaseurl.replace(
                "://", f"://{gogsadmin[0]}:{gogsadmin[1]}@"
            )
            self.gogsauth = gogs_client.UsernamePassword(gogsadmin[0], gogsadmin[1])
            self.gogsapi = gogs_client.GogsApi(gogsbaseurl)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.backupfile.close()

    def __iter__(self):
        return iter(
            self.GitLabRepo(bundle=info[0], membership=info[1][0], name=info[1][1])
            for info in filter(
                lambda fi: len(fi) > 1,
                (
                    (fi.name, *re.findall(r"^repositories/(.*)/(.*)\.bundle$", fi.name))
                    for fi in self.backupfile.getmembers()
                ),
            )
        )

    def importUserRepo(self, gitlabrepo, repo):
        if self.gogsapi.repo_exists(
            self.gogsauth, gitlabrepo.membership, gitlabrepo.name
        ):
            click.echo(
                f"WARNING: Repository {gitlabrepo.name} "
                + f"for user {gitlabrepo.membership} already exists - skipping!"
            )
            return

        click.echo(
            f"Importing user repository: {gitlabrepo.name} ({gitlabrepo.bundle})"
        )

        userauth = self.gogsapi.ensure_token(
            self.gogsauth, "gitlab2gogs", gitlabrepo.membership
        )
        self.gogsapi.create_repo(
            userauth,
            gitlabrepo.name,
            datetime.today().strftime("Imported from GitLab-backup on %x at %X"),
        )

        repo.git.push(
            "--mirror",
            f"{self.gogsbaseurl}/{gitlabrepo.membership}/{gitlabrepo.name}.git",
        )

    def importGroupRepo(self, gitlabrepo, repo):
        if self.gogsapi.repo_exists(
            self.gogsauth, self.gogsauth.username, gitlabrepo.name
        ):
            click.echo(
                f"WARNING: Repository {gitlabrepo.name} "
                + f"for user {self.gogsauth.username} already exists - skipping!"
            )
            return

        click.echo(
            f"Importing organization repository: {gitlabrepo.name} ({gitlabrepo.bundle})"
        )

        userauth = self.gogsapi.ensure_token(
            self.gogsauth, "gitlab2gogs", self.gogsauth.username
        )
        self.gogsapi.create_repo(
            userauth,
            gitlabrepo.name,
            datetime.today().strftime("Imported from GitLab-backup on %x at %X"),
            organization=self.organizationNameFromGroupName(gitlabrepo.membership),
        )

        repo.git.push(
            "--mirror",
            f"{self.gogsbaseurl}/{self.organizationNameFromGroupName(gitlabrepo.membership)}/{gitlabrepo.name}.git",
        )

    def organizationNameFromGroupName(self, name):
        return (
            name
            if name.isalnum()
            else re.sub(r"[^0-9a-zA-Z\-_\.\/]", "_", name).replace("/", ".")
        )

    def isUserRepo(self, gitlabrepo):
        return gitlabrepo.membership.lower() in self.usernames

    def analyzeRepos(self):
        organizations = {}
        users = {}

        for gitlabrepo in self:
            if self.isUserRepo(gitlabrepo):
                users[gitlabrepo.membership] = gitlabrepo.membership
            else:
                organizations[
                    gitlabrepo.membership
                ] = self.organizationNameFromGroupName(gitlabrepo.membership)

        if len(users) > 0:
            click.echo("Users:")
            for name in users:
                click.echo(f"- {name}")

        if len(organizations) > 0:
            click.echo("Organizations (GitLab -> Gogs):")
            for key, name in organizations.items():
                click.echo(f"- {key} -> {name}")

    def importRepos(self):
        for gitlabrepo in self:
            self.importRepo(gitlabrepo)

    def importRepo(self, gitlabrepo):
        def remove_readonly(func, path, _):
            os.chmod(path, stat.S_IWRITE)
            func(path)

        with tempfile.TemporaryDirectory() as tempdir:
            try:
                temppath = Path(str(tempdir))

                bundlepath = str(temppath / gitlabrepo.bundle)
                clonedestpath = str(temppath / "clone")

                self.backupfile.extract(gitlabrepo.bundle, str(temppath))
                with Repo.clone_from(bundlepath, clonedestpath) as repo:
                    if self.isUserRepo(gitlabrepo):
                        self.importUserRepo(gitlabrepo, repo)
                    else:
                        self.importGroupRepo(gitlabrepo, repo)
            finally:
                shutil.rmtree(clonedestpath, onerror=remove_readonly)


@click.group()
def main():
    return True


@main.command("analyze")
@click.argument("backupfile", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--user",
    "-u",
    multiple=True,
    help="Gitlab username to distinguish between groups and users",
)
def analyzeRepos(backupfile, user):
    with GitlabBackup(click.format_filename(backupfile), user) as gitlabBackup:
        gitlabBackup.analyzeRepos()


@main.command("import")
@click.argument("backupfile", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--user",
    "-u",
    multiple=True,
    help="Gitlab username to distinguish between groups and users",
)
@click.option(
    "--gogsurl",
    type=click.STRING,
    required=True,
    help="Gogs-baseurl to add repositories",
)
@click.option(
    "--gogsadmin",
    type=click.STRING,
    required=True,
    help="Gogs-admin user to add repositories/users/organizations",
)
@click.option(
    "--gogspassword",
    type=click.STRING,
    prompt=True,
    hide_input=True,
    help="Gogs-admin user password to add repositories/users/organizations",
)
def importRepos(backupfile, user, gogsurl, gogsadmin, gogspassword):
    with GitlabBackup(
        click.format_filename(backupfile), user, gogsurl, (gogsadmin, gogspassword)
    ) as gitlabBackup:
        gitlabBackup.importRepos()


if __name__ == "__main__":
    main(obj={})
