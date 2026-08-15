plugins {
    kotlin("jvm") version "2.0.21"
    `maven-publish`
}

group = "com.spearsystems"
version = "0.1.0"

repositories { mavenCentral() }

dependencies {
    implementation("com.networknt:json-schema-validator:1.5.6")
    testImplementation(kotlin("test"))
}

kotlin { jvmToolchain(17) }

publishing {
    publications {
        create<MavenPublication>("mavenJava") {
            from(components["java"])
            pom {
                name.set("LCP Kotlin SDK")
                description.set("LCP Lead Context Protocol SDK for Kotlin")
                url.set("https://github.com/SpearSystems/LCP")
                licenses { license { name.set("Apache-2.0"); url.set("https://www.apache.org/licenses/LICENSE-2.0") } }
            }
        }
    }
    repositories {
        maven {
            name = "release"
            url = uri(System.getenv("MAVEN_REPOSITORY_URL") ?: layout.buildDirectory.dir("repository"))
            credentials {
                username = System.getenv("MAVEN_USERNAME")
                password = System.getenv("MAVEN_PASSWORD")
            }
        }
    }
}
